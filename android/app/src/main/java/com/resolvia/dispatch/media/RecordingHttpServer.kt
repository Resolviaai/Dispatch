package com.resolvia.dispatch.media

import android.content.Context
import android.util.Log
import com.resolvia.dispatch.sync.ByteRangeResult
import com.resolvia.dispatch.sync.RecordingMediaPolicy
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.InputStream
import java.io.RandomAccessFile
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLEncoder
import java.security.SecureRandom
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.ThreadFactory
import java.util.concurrent.atomic.AtomicInteger

/**
 * Loopback HTTP/1.1 server for recorded segments and their posters.
 *
 * Why this exists: media was previously served through
 * `WebViewClient.shouldInterceptRequest`, which is not an HTTP connection. A media
 * player opens several concurrent, overlapping range requests (head probe, tail for
 * the `moov` atom, then sequential reads) and abandons the ones it no longer needs.
 * An intercepted `WebResourceResponse` cannot express that: Chromium serialises the
 * requests, rewrites or drops the `Content-Length`/`Content-Range` we set, and the
 * player aborts with no `MediaError` at all. That surfaced as "playback failed" for
 * large recordings while small ones happened to survive.
 *
 * Serving the same bytes over a real socket gives genuine 206 semantics, concurrent
 * connections and ordinary seeks, so the player behaves exactly as it would against
 * any web server.
 *
 * Security:
 * - Bound to 127.0.0.1 only, never a LAN interface.
 * - Every request must carry the per-process random token in its path, so another
 *   app on the device cannot read private recordings just by scanning loopback.
 * - File resolution goes through [RecordingMediaPolicy.resolveRecordingPath], which
 *   rejects traversal, separators, drive/drive-colon tricks and in-flight .tmp files,
 *   and confines the result to the recordings directories.
 * - GET/HEAD only; no directory listing, no writes.
 */
object RecordingHttpServer {

    private const val TAG = "RecordingMedia"
    private const val HOST = "127.0.0.1"
    private const val CONNECTION_TIMEOUT_MS = 10_000
    private const val MAX_LINE = 8 * 1024
    private const val WORKER_COUNT = 6
    private const val SHELL_ORIGIN = "http://appassets.androidplatform.net"

    /** Hard cap on requests served over a single keep-alive connection. */
    internal const val MAX_REQUESTS_PER_CONNECTION = 100

    @Volatile private var serverSocket: ServerSocket? = null
    @Volatile private var acceptThread: Thread? = null
    @Volatile private var pool: ExecutorService? = null
    @Volatile private var token: String = ""
    @Volatile private var port: Int = 0
    @Volatile private var roots: List<File> = emptyList()

    /** Starts the server if needed. Returns true when it is accepting connections. */
    @Synchronized
    fun ensureStarted(context: Context): Boolean {
        if (serverSocket != null && port > 0 && token.isNotEmpty()) return true
        return try {
            val app = context.applicationContext
            roots = listOf(
                File(app.getExternalFilesDir(null) ?: app.filesDir, "recordings").apply { if (!exists()) mkdirs() },
                File(app.filesDir, "recordings").apply { if (!exists()) mkdirs() }
            )
            val socket = ServerSocket(0, 8, InetAddress.getByName(HOST))
            serverSocket = socket
            port = socket.localPort
            token = randomToken()

            val counter = AtomicInteger(0)
            val factory = ThreadFactory { runnable ->
                Thread(runnable, "recording-http-${counter.incrementAndGet()}").apply { isDaemon = true }
            }
            val executor = Executors.newFixedThreadPool(WORKER_COUNT, factory)
            pool = executor

            acceptThread = Thread({ acceptLoop(socket, executor) }, "recording-http-accept").apply {
                isDaemon = true
                start()
            }
            Log.i(TAG, "serving recordings on $HOST:$port")
            true
        } catch (e: Exception) {
            Log.e(TAG, "loopback media server failed to start: ${e.message}")
            stop()
            false
        }
    }

    /** Absolute URL for a recording file, or null when the server is not running. */
    fun mediaUrl(fileName: String): String? {
        val activePort = port
        val activeToken = token
        if (activePort <= 0 || activeToken.isEmpty() || fileName.isBlank()) return null
        val encoded = URLEncoder.encode(fileName, "UTF-8").replace("+", "%20")
        return "http://$HOST:$activePort/$activeToken/recordings/$encoded"
    }

    @Synchronized
    fun stop() {
        try {
            serverSocket?.close()
        } catch (_: Exception) {
        }
        serverSocket = null
        acceptThread?.interrupt()
        acceptThread = null
        pool?.shutdownNow()
        pool = null
        port = 0
        token = ""
    }

    private fun randomToken(): String {
        val bytes = ByteArray(16)
        SecureRandom().nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
    }

    private fun acceptLoop(socket: ServerSocket, executor: ExecutorService) {
        while (!socket.isClosed) {
            val client = try {
                socket.accept()
            } catch (e: Exception) {
                if (socket.isClosed) return
                Log.w(TAG, "accept failed: ${e.message}")
                continue
            }
            executor.execute { handle(client) }
        }
    }

    private fun handle(client: Socket) {
        handleConnection(client, token, roots)
    }

    /**
     * Serves requests on one socket. Conservative HTTP/1.1 keep-alive: requests
     * are read sequentially from the same socket while the client permits reuse
     * (HTTP/1.1 default, HTTP/1.0 opt-in via `Connection: keep-alive`) and a
     * reusable media response was served. Error replies (400/403/404/405/416)
     * keep the previous close-after-response behaviour. Connection lifetime is
     * bounded by [MAX_REQUESTS_PER_CONNECTION] and the socket read timeout, so
     * an idle keep-alive connection is reaped after [CONNECTION_TIMEOUT_MS].
     *
     * One [RandomAccessFile] is opened per connection and reused across requests
     * for the same file (re-opened when a request targets another file), closing
     * in `finally` — a scrubbing player no longer pays an open/close per range
     * request. Sequential range requests on one connection each get correct 206
     * responses.
     *
     * Internal (not private) so JVM unit tests can drive the loop directly.
     */
    internal fun handleConnection(client: Socket, activeToken: String, activeRoots: List<File>) {
        try {
            client.soTimeout = CONNECTION_TIMEOUT_MS
            client.use { socket ->
                val input = BufferedInputStream(socket.getInputStream())
                val output = BufferedOutputStream(socket.getOutputStream())
                var raf: RandomAccessFile? = null
                var rafTarget: File? = null
                try {
                    var requestsServed = 0
                    while (requestsServed < MAX_REQUESTS_PER_CONNECTION) {
                        val requestLine = readLine(input) ?: return
                        val headers = readHeaders(input)

                        val parts = requestLine.split(' ')
                        if (parts.size < 2) return writeText(output, 400, "Bad Request")
                        val method = parts[0].uppercase()
                        if (method != "GET" && method != "HEAD") {
                            return writeText(output, 405, "Method Not Allowed")
                        }
                        val target = parts[1].substringBefore('?')
                        val prefix = "/$activeToken/recordings/"
                        if (!target.startsWith(prefix)) {
                            // Wrong/absent token: do not reveal whether a file exists.
                            return writeText(output, 403, "Forbidden")
                        }
                        val requestedName = target.removePrefix(prefix)
                        val file = RecordingMediaPolicy.resolveRecordingPath(requestedName, activeRoots[0], activeRoots[1])
                            ?: return writeText(output, 404, "Not Found")
                        if (!file.isFile || file.length() <= 0L) {
                            return writeText(output, 404, "Not Found")
                        }

                        if (raf == null || rafTarget !== file) {
                            try { raf?.close() } catch (_: Exception) {}
                            raf = RandomAccessFile(file, "r")
                            rafTarget = file
                        }

                        val keepAlive = clientWantsKeepAlive(requestLine, headers.connection)
                        val persisted = serveMedia(
                            output, raf, file, headers.range,
                            method == "HEAD", keepAlive = keepAlive
                        )
                        if (!persisted) return
                        requestsServed++
                    }
                } finally {
                    try { raf?.close() } catch (_: Exception) {}
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "request failed: ${e.message}")
        }
    }

    /**
     * Conservative keep-alive decision: HTTP/1.1 defaults to reuse unless the
     * client sends `Connection: close`; HTTP/1.0 must opt in; unknown or absent
     * versions always close.
     */
    private fun clientWantsKeepAlive(requestLine: String, connectionHeader: String?): Boolean {
        val directives = connectionHeader?.split(',')?.map { it.trim() } ?: emptyList()
        if (directives.any { it.equals("close", ignoreCase = true) }) return false
        val version = requestLine.split(' ').getOrNull(2) ?: return false
        return when {
            version.contains("HTTP/1.1") -> true
            version.contains("HTTP/1.0") -> directives.any { it.equals("keep-alive", ignoreCase = true) }
            else -> false
        }
    }

    /**
     * Writes one media response. Returns true when the connection may persist
     * for another request (client keep-alive plus a successful 200/206 reply);
     * false when it must close (client requested close, or an unsatisfiable /
     * non-reusable reply was sent).
     */
    private fun serveMedia(
        output: BufferedOutputStream,
        raf: RandomAccessFile,
        file: File,
        rangeHeader: String?,
        headOnly: Boolean,
        keepAlive: Boolean
    ): Boolean {
        val length = file.length()
        val mime = RecordingMediaPolicy.mediaMimeFor(file.name)
        val resolved = RecordingMediaPolicy.resolveRange(rangeHeader, length)

        if (resolved is ByteRangeResult.Unsatisfiable) {
            val body = "Range Not Satisfiable".toByteArray()
            writeHead(
                output, 416, "Range Not Satisfiable", mime, body.size.toLong(),
                extra = "Content-Range: bytes */$length\r\n"
            )
            if (!headOnly) output.write(body)
            output.flush()
            return false
        }

        val start: Long
        val end: Long
        val status: Int
        val reason: String
        var extra = ""
        if (resolved is ByteRangeResult.Satisfiable) {
            start = resolved.start
            end = resolved.end
            status = 206
            reason = "Partial Content"
            extra = "Content-Range: bytes $start-$end/$length\r\n"
        } else {
            start = 0L
            end = length - 1
            status = 200
            reason = "OK"
        }
        val contentLength = end - start + 1
        writeHead(output, status, reason, mime, contentLength, extra, keepAlive = keepAlive)
        if (!headOnly) {
            // Plain chunked copy: predictable memory use and no dependence on
            // FileChannel.transferTo semantics against a socket-backed stream.
            val buffer = ByteArray(64 * 1024)
            raf.seek(start)
            var remaining = contentLength
            while (remaining > 0) {
                val wanted = minOf(buffer.size.toLong(), remaining).toInt()
                val read = raf.read(buffer, 0, wanted)
                if (read <= 0) break
                output.write(buffer, 0, read)
                remaining -= read
            }
        }
        output.flush()
        return keepAlive
    }

    private fun writeHead(
        output: BufferedOutputStream,
        status: Int,
        reason: String,
        mime: String,
        contentLength: Long,
        extra: String = "",
        keepAlive: Boolean = false
    ) {
        val head = buildString {
            append("HTTP/1.1 $status $reason\r\n")
            append("Content-Type: $mime\r\n")
            append("Content-Length: $contentLength\r\n")
            append("Accept-Ranges: bytes\r\n")
            append(extra)
            append("Cache-Control: no-store\r\n")
            append("Access-Control-Allow-Origin: $SHELL_ORIGIN\r\n")
            append("Vary: Origin\r\n")
            append("Connection: ${if (keepAlive) "keep-alive" else "close"}\r\n")
            append("\r\n")
        }
        output.write(head.toByteArray(Charsets.US_ASCII))
    }

    private fun writeText(output: BufferedOutputStream, status: Int, message: String) {
        val body = message.toByteArray()
        writeHead(output, status, message, "text/plain; charset=utf-8", body.size.toLong())
        output.write(body)
        output.flush()
    }

    private fun readLine(input: InputStream): String? {
        val buffer = StringBuilder()
        while (true) {
            val b = input.read()
            if (b == -1) return if (buffer.isEmpty()) null else buffer.toString()
            if (b == '\n'.code) return buffer.toString().trimEnd('\r')
            if (buffer.length >= MAX_LINE) return null
            buffer.append(b.toChar())
        }
    }

    /** Request headers the connection loop needs. */
    private class RequestHeaders(val range: String?, val connection: String?)

    /** Consumes headers, returning the Range and Connection values when present. */
    private fun readHeaders(input: InputStream): RequestHeaders {
        var range: String? = null
        var connection: String? = null
        while (true) {
            val line = readLine(input) ?: return RequestHeaders(range, connection)
            if (line.isEmpty()) return RequestHeaders(range, connection)
            val separator = line.indexOf(':')
            if (separator <= 0) continue
            val name = line.substring(0, separator).trim().lowercase()
            val value = line.substring(separator + 1).trim()
            when (name) {
                "range" -> range = value
                "connection" -> connection = value
            }
        }
    }
}
