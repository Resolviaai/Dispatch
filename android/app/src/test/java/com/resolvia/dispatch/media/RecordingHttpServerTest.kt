package com.resolvia.dispatch.media

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files
import kotlin.concurrent.thread

/**
 * JVM regression coverage for the loopback media server's conservative
 * HTTP/1.1 keep-alive connection loop: sequential range scrubs on one socket
 * each get correct 206 responses, `Connection: close` is honoured, non-media
 * responses keep the close-after-reply behaviour, and one RandomAccessFile is
 * reused per connection (re-opened when a request targets another file).
 */
class RecordingHttpServerTest {

    private val tempDirectory = Files.createTempDirectory("dispatch-recording-http-test").toFile()

    private companion object {
        const val TOKEN = "testtoken"
    }

    private fun newRoot(): File {
        val root = File(tempDirectory, "recordings-" + java.util.UUID.randomUUID())
        root.mkdirs()
        return root
    }

    private class ParsedResponse(
        val statusLine: String,
        val headers: Map<String, String>,
        val body: ByteArray
    ) {
        val status: Int = statusLine.split(' ')[1].toInt()

        fun header(name: String): String? =
            headers.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value
    }

    private fun readResponse(input: BufferedInputStream, expectBody: Boolean = true): ParsedResponse {
        val headerBytes = ByteArrayOutputStream()
        var state = 0
        while (true) {
            val b = input.read()
            assertTrue("EOF while reading response headers (state=$state)", b != -1)
            headerBytes.write(b)
            state = when {
                state == 0 && b == '\r'.code -> 1
                state == 1 && b == '\n'.code -> 2
                state == 2 && b == '\r'.code -> 3
                state == 3 && b == '\n'.code -> break
                b == '\r'.code -> 1
                else -> 0
            }
        }
        val headerText = headerBytes.toString(Charsets.UTF_8.name())
        val lines = headerText.split("\r\n")
        val headers = LinkedHashMap<String, String>()
        for (line in lines.drop(1)) {
            val idx = line.indexOf(':')
            if (idx > 0) {
                headers[line.substring(0, idx).trim()] = line.substring(idx + 1).trim()
            }
        }
        val contentLength = headers.entries
            .firstOrNull { it.key.equals("Content-Length", ignoreCase = true) }
            ?.value?.toLongOrNull() ?: 0L
        val body = if (expectBody && contentLength > 0) {
            val buf = ByteArray(contentLength.toInt())
            var offset = 0
            while (offset < contentLength) {
                val read = input.read(buf, offset, (contentLength - offset).toInt())
                assertTrue("EOF while reading response body", read != -1)
                offset += read
            }
            buf
        } else {
            ByteArray(0)
        }
        return ParsedResponse(lines.first(), headers, body)
    }

    private fun withConnection(
        root: File,
        block: (client: Socket, input: BufferedInputStream, out: java.io.OutputStream) -> Unit
    ) {
        val server = ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"))
        val handler = thread(start = true, isDaemon = true) {
            try {
                val socket = server.accept()
                RecordingHttpServer.handleConnection(socket, TOKEN, listOf(root, root))
            } catch (_: Exception) {
                // Accept/serve failure: the client-side assertions will fail with detail.
            }
        }
        try {
            val client = Socket(InetAddress.getByName("127.0.0.1"), server.localPort)
            client.soTimeout = 10_000
            client.use { sock ->
                block(sock, BufferedInputStream(sock.getInputStream()), sock.getOutputStream())
            }
        } finally {
            server.close()
            handler.join(5000)
        }
    }

    @Test
    fun keepAliveConnection_servesSequentialRangeRequests_onOneSocket() {
        val root = newRoot()
        val payload = ByteArray(1024) { (it % 251).toByte() }
        File(root, "seg_keep.mp4").writeBytes(payload)

        withConnection(root) { _, input, out ->
            // Request 1: full-body GET on HTTP/1.1 (keep-alive by default).
            out.write("GET /$TOKEN/recordings/seg_keep.mp4 HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray())
            out.flush()
            val first = readResponse(input)
            assertEquals(200, first.status)
            assertEquals(payload.size.toLong().toString(), first.header("Content-Length"))
            assertArrayEquals(payload, first.body)
            assertEquals("keep-alive", first.header("Connection")?.lowercase())

            // Request 2: sequential range scrub on the SAME connection.
            out.write("GET /$TOKEN/recordings/seg_keep.mp4 HTTP/1.1\r\nHost: localhost\r\nRange: bytes=100-203\r\n\r\n".toByteArray())
            out.flush()
            val second = readResponse(input)
            assertEquals(206, second.status)
            assertEquals("bytes 100-203/1024", second.header("Content-Range"))
            assertEquals(104L.toString(), second.header("Content-Length"))
            assertArrayEquals(payload.copyOfRange(100, 204), second.body)
            assertEquals("keep-alive", second.header("Connection")?.lowercase())

            // Request 3: HEAD with explicit close must end the connection.
            out.write("HEAD /$TOKEN/recordings/seg_keep.mp4 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".toByteArray())
            out.flush()
            val third = readResponse(input, expectBody = false)
            assertEquals(200, third.status)
            assertEquals("close", third.header("Connection")?.lowercase())
            assertEquals(-1, input.read())
        }
    }

    @Test
    fun http10DefaultsToClose_unlessKeepAliveIsOptedIn() {
        val root = newRoot()
        val payload = ByteArray(64) { it.toByte() }
        File(root, "seg_http10.mp4").writeBytes(payload)

        // HTTP/1.0 without an explicit opt-in: close after one response.
        withConnection(root) { _, input, out ->
            out.write("GET /$TOKEN/recordings/seg_http10.mp4 HTTP/1.0\r\n\r\n".toByteArray())
            out.flush()
            val resp = readResponse(input)
            assertEquals(200, resp.status)
            assertArrayEquals(payload, resp.body)
            assertEquals("close", resp.header("Connection")?.lowercase())
            assertEquals(-1, input.read())
        }

        // HTTP/1.0 with `Connection: keep-alive`: reuse is honoured.
        withConnection(root) { _, input, out ->
            out.write("GET /$TOKEN/recordings/seg_http10.mp4 HTTP/1.0\r\nConnection: keep-alive\r\n\r\n".toByteArray())
            out.flush()
            val first = readResponse(input)
            assertEquals(200, first.status)
            assertEquals("keep-alive", first.header("Connection")?.lowercase())

            out.write("GET /$TOKEN/recordings/seg_http10.mp4 HTTP/1.0\r\nConnection: keep-alive\r\n\r\n".toByteArray())
            out.flush()
            val second = readResponse(input)
            assertEquals(200, second.status)
            assertArrayEquals(payload, second.body)
        }
    }

    @Test
    fun notFoundResponse_keepsCloseAfterReplyBehaviour() {
        val root = newRoot()
        File(root, "seg_real.mp4").writeBytes(byteArrayOf(1, 2, 3))

        withConnection(root) { _, input, out ->
            out.write("GET /$TOKEN/recordings/ghost.mp4 HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray())
            out.flush()
            val resp = readResponse(input)
            assertEquals(404, resp.status)
            assertEquals("close", resp.header("Connection")?.lowercase())
            assertEquals(-1, input.read())
        }
    }

    @Test
    fun keepAliveSwitchingFiles_servesCorrectBytesForEachFile() {
        val root = newRoot()
        val payloadA = ByteArray(512) { ((it * 7) % 251).toByte() }
        val payloadB = ByteArray(300) { ((it * 13) % 251).toByte() }
        File(root, "seg_a.mp4").writeBytes(payloadA)
        File(root, "seg_b.mp4").writeBytes(payloadB)

        withConnection(root) { _, input, out ->
            out.write("GET /$TOKEN/recordings/seg_a.mp4 HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray())
            out.flush()
            val first = readResponse(input)
            assertEquals(200, first.status)
            assertArrayEquals(payloadA, first.body)

            // Different file on the same connection: the per-connection
            // RandomAccessFile must be re-opened, not re-seeked across files.
            out.write("GET /$TOKEN/recordings/seg_b.mp4 HTTP/1.1\r\nHost: localhost\r\nRange: bytes=10-19\r\n\r\n".toByteArray())
            out.flush()
            val second = readResponse(input)
            assertEquals(206, second.status)
            assertEquals("bytes 10-19/300", second.header("Content-Range"))
            assertArrayEquals(payloadB.copyOfRange(10, 20), second.body)
        }
    }

    @Test
    fun connectionIsHardCapped_atMaxRequestsPerConnection() {
        val root = newRoot()
        val payload = ByteArray(32) { it.toByte() }
        File(root, "seg_cap.mp4").writeBytes(payload)

        withConnection(root) { _, input, out ->
            repeat(RecordingHttpServer.MAX_REQUESTS_PER_CONNECTION) { index ->
                out.write("GET /$TOKEN/recordings/seg_cap.mp4 HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray())
                out.flush()
                val resp = readResponse(input)
                assertEquals("request #$index", 200, resp.status)
            }
            // Cap reached: the server closes the connection instead of serving forever.
            assertEquals(-1, input.read())
        }
    }
}
