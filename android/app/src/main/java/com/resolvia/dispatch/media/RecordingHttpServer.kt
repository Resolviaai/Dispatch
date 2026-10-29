package com.resolvia.dispatch.media

import android.content.Context
import android.util.Log
import com.resolvia.dispatch.sync.ByteRangeResult
import com.resolvia.dispatch.sync.RecordingMediaPolicy
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.InputStream
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
        try {
            client.soTimeout = CONNECTION_TIMEOUT_MS
            client.use { socket ->
                val input = BufferedInputStream(socket.getInputStream())
                val output = BufferedOutputStream(socket.getOutputStream())

                val requestLine = readLine(input) ?: return
                val rangeHeader = readHeaders(input)

                val parts = requestLine.split(' ')
                if (parts.size < 2) return writeText(output, 400, "Bad Request")
                val method = parts[0].uppercase()
                if (method != "GET" && method != "HEAD") {
                    return writeText(output, 405, "Method Not Allowed")
                }
                val target = parts[1].substringBefore('?')
                val prefix = "/$token/recordings/"
                if (!target.startsWith(prefix)) {
                    // Wrong/absent token: do not reveal whether a file exists.
                    return writeText(output, 403, "Forbidden")
                }
                val requestedName = target.removePrefix(prefix)
                val file = RecordingMediaPolicy.resolveRecordingPath(requestedName, roots[0], roots[1])
                    ?: return writeText(output, 404, "Not Found")
                if (!file.isFile || file.length() <= 0L) {
                    return writeText(output, 404, "Not Found")
                }

                serveMedia(socket, output, file, rangeHeader, method == "HEAD")
            }
        } catch (e: Exception) {
            Log.w(TAG, "request failed: ${e.message}")
        }
    }

    private fun serveMedia(
        socket: Socket,
        output: BufferedOutputStream,
        file: File,
        rangeHeader: String?,
        headOnly: Boolean
    ) {
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
            return
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
        writeHead(output, status, reason, mime, contentLength, extra)
        if (!headOnly) {
            // Plain chunked copy: predictable memory use and no dependence on
            // FileChannel.transferTo semantics against a socket-backed stream.
            val buffer = ByteArray(64 * 1024)
            java.io.RandomAccessFile(file, "r").use { raf ->
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
        }
        output.flush()
    }

    private fun writeHead(
        output: BufferedOutputStream,
        status: Int,
        reason: String,
        mime: String,
        contentLength: Long,
        extra: String = ""
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
            append("Connection: close\r\n")
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

    /** Consumes headers, returning the (lower-cased) Range value when present. */
    private fun readHeaders(input: InputStream): String? {
        var range: String? = null
        while (true) {
            val line = readLine(input) ?: return range
            if (line.isEmpty()) return range
            val separator = line.indexOf(':')
            if (separator <= 0) continue
            val name = line.substring(0, separator).trim().lowercase()
            val value = line.substring(separator + 1).trim()
            if (name == "range") range = value
        }
    }
}
