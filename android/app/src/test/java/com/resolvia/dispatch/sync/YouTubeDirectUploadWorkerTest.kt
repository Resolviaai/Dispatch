package com.resolvia.dispatch.sync

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.RequestBody.Companion.toRequestBody
import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class YouTubeDirectUploadWorkerTest {

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .build()

    private data class CapturedHttpRequest(
        val method: String,
        val path: String,
        val query: String?,
        val headers: Map<String, String>,
        val body: String
    ) {
        val fullTarget: String get() = if (query.isNullOrEmpty()) path else "$path?$query"
        fun header(name: String): String? =
            headers.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value
    }

    private data class StubHttpResponse(
        val statusCode: Int,
        val headers: Map<String, String> = emptyMap(),
        val body: String = ""
    )

    private fun readHttpRequest(input: BufferedInputStream): CapturedHttpRequest {
        val headerBytes = ByteArrayOutputStream()
        var state = 0
        while (true) {
            val b = input.read()
            if (b == -1) break
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
        val requestLineParts = lines.firstOrNull().orEmpty().split(" ")
        val method = requestLineParts.getOrNull(0).orEmpty()
        val rawTarget = requestLineParts.getOrNull(1).orEmpty()
        val path = rawTarget.substringBefore('?')
        val query = if ('?' in rawTarget) rawTarget.substringAfter('?') else null

        val headers = LinkedHashMap<String, String>()
        for (line in lines.drop(1)) {
            val idx = line.indexOf(':')
            if (idx > 0) {
                headers[line.substring(0, idx).trim()] = line.substring(idx + 1).trim()
            }
        }
        val contentLength = headers.entries
            .firstOrNull { it.key.equals("Content-Length", ignoreCase = true) }
            ?.value?.toIntOrNull() ?: 0
        val bodyBytes = if (contentLength > 0) {
            val buf = ByteArray(contentLength)
            var offset = 0
            while (offset < contentLength) {
                val read = input.read(buf, offset, contentLength - offset)
                if (read == -1) break
                offset += read
            }
            buf.copyOf(offset)
        } else {
            ByteArray(0)
        }
        return CapturedHttpRequest(
            method = method,
            path = path,
            query = query,
            headers = headers,
            body = bodyBytes.toString(Charsets.UTF_8)
        )
    }

    private fun withHttpServer(
        handler: (port: Int, req: CapturedHttpRequest) -> StubHttpResponse,
        block: (String) -> Unit
    ) {
        val server = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        val running = AtomicBoolean(true)
        val port = server.localPort
        val worker = Thread {
            while (running.get()) {
                try {
                    val socket = server.accept()
                    socket.use { sock ->
                        val input = BufferedInputStream(sock.getInputStream())
                        val req = readHttpRequest(input)
                        val resp = handler(port, req)
                        val bodyBytes = resp.body.toByteArray(Charsets.UTF_8)
                        val out = sock.getOutputStream()
                        val sb = StringBuilder()
                        sb.append("HTTP/1.1 ${resp.statusCode} OK\r\n")
                        for ((k, v) in resp.headers) {
                            sb.append("$k: $v\r\n")
                        }
                        if (resp.statusCode != 204 && resp.statusCode != 304) {
                            sb.append("Content-Length: ${bodyBytes.size}\r\n")
                        }
                        sb.append("Connection: close\r\n\r\n")
                        out.write(sb.toString().toByteArray(Charsets.UTF_8))
                        if (bodyBytes.isNotEmpty() && resp.statusCode != 204 && resp.statusCode != 304) {
                            out.write(bodyBytes)
                        }
                        out.flush()
                    }
                } catch (_: SocketException) {
                    if (!running.get()) break
                } catch (_: Exception) {
                    // Ignore client tear-down errors during shutdown
                }
            }
        }
        worker.isDaemon = true
        worker.start()
        try {
            block("http://127.0.0.1:$port")
        } finally {
            running.set(false)
            runCatching { server.close() }
            worker.join(2000)
        }
    }

    @Test
    fun queryUploadStatus_returnsConfirmedComplete_on200WithVideoId() {
        withHttpServer({ _, _ ->
            StubHttpResponse(
                statusCode = 200,
                headers = mapOf("Content-Type" to "application/json"),
                body = """{"id":"yt_video_789"}"""
            )
        }) { baseUrl ->
            val result = YouTubeDirectUploadWorker.queryUploadStatus(
                client = client,
                uploadUrl = "$baseUrl/upload/session",
                accessToken = "oauth_tok",
                totalBytes = 4096L
            )
            assertEquals(
                YouTubeDirectUploadWorker.UploadProbeResult.ConfirmedComplete("yt_video_789"),
                result
            )
        }
    }

    @Test
    fun queryUploadStatus_returnsResumableAt_on308WithAndWithoutRange() {
        withHttpServer({ _, _ ->
            StubHttpResponse(
                statusCode = 308,
                headers = mapOf("Range" to "bytes=0-2047")
            )
        }) { baseUrl ->
            val result = YouTubeDirectUploadWorker.queryUploadStatus(
                client = client,
                uploadUrl = "$baseUrl/upload/session",
                accessToken = "",
                totalBytes = 4096L
            )
            assertEquals(YouTubeDirectUploadWorker.UploadProbeResult.ResumableAt(2048L), result)
        }

        withHttpServer({ _, _ ->
            StubHttpResponse(statusCode = 308)
        }) { baseUrl ->
            val result = YouTubeDirectUploadWorker.queryUploadStatus(
                client = client,
                uploadUrl = "$baseUrl/upload/session",
                accessToken = "",
                totalBytes = 4096L
            )
            assertEquals(YouTubeDirectUploadWorker.UploadProbeResult.ResumableAt(0L), result)
        }
    }

    @Test
    fun queryUploadStatus_returnsExpired_onlyOnAuthoritative404_410_orCloud401() {
        for (code in listOf(404, 410)) {
            withHttpServer({ _, _ ->
                StubHttpResponse(statusCode = code)
            }) { baseUrl ->
                val result = YouTubeDirectUploadWorker.queryUploadStatus(
                    client = client,
                    uploadUrl = "$baseUrl/upload/session",
                    accessToken = "oauth_tok",
                    totalBytes = 4096L
                )
                assertEquals(YouTubeDirectUploadWorker.UploadProbeResult.Expired, result)
            }
        }

        // Empty accessToken identifies a Worker-minted cloud URL -> 401 means invalidated session URL
        withHttpServer({ _, _ ->
            StubHttpResponse(statusCode = 401)
        }) { baseUrl ->
            val result = YouTubeDirectUploadWorker.queryUploadStatus(
                client = client,
                uploadUrl = "$baseUrl/upload/session",
                accessToken = "",
                totalBytes = 4096L
            )
            assertEquals(YouTubeDirectUploadWorker.UploadProbeResult.Expired, result)
        }
    }

    @Test
    fun queryUploadStatus_returnsNull_onTransient5xx_rateLimit_malformedRange_andNetworkError() {
        // 500, 502, 503, 504, 429 MUST return null (never Expired and never ResumableAt(0L))
        for (code in listOf(408, 429, 500, 502, 503, 504)) {
            withHttpServer({ _, _ ->
                StubHttpResponse(statusCode = code)
            }) { baseUrl ->
                val result = YouTubeDirectUploadWorker.queryUploadStatus(
                    client = client,
                    uploadUrl = "$baseUrl/upload/session",
                    accessToken = "oauth_tok",
                    totalBytes = 10_000L
                )
                assertNull("HTTP $code probe must return null instead of $result", result)
            }
        }

        // Malformed / non-zero-start Range header MUST return null instead of resetting offset to 0
        withHttpServer({ _, _ ->
            StubHttpResponse(
                statusCode = 308,
                headers = mapOf("Range" to "bytes=500-2000")
            )
        }) { baseUrl ->
            val result = YouTubeDirectUploadWorker.queryUploadStatus(
                client = client,
                uploadUrl = "$baseUrl/upload/session",
                accessToken = "",
                totalBytes = 10_000L
            )
            assertNull(result)
        }

        // Unreachable socket / connection refused MUST return null cleanly
        val unusedPort = ServerSocket(0).use { it.localPort }
        val networkErrResult = YouTubeDirectUploadWorker.queryUploadStatus(
            client = client,
            uploadUrl = "http://127.0.0.1:$unusedPort/upload/session",
            accessToken = "",
            totalBytes = 10_000L
        )
        assertNull(networkErrResult)
    }

    @Test
    fun queryUploadStatus_refreshesExpiredOAuthTokenOnceOn401() {
        val attempts = AtomicInteger(0)
        withHttpServer({ _, req ->
            val auth = req.header("Authorization")
            if (attempts.incrementAndGet() == 1 && auth == "Bearer stale_tok") {
                StubHttpResponse(statusCode = 401)
            } else if (auth == "Bearer fresh_tok") {
                StubHttpResponse(
                    statusCode = 308,
                    headers = mapOf("Range" to "bytes=0-1023")
                )
            } else {
                StubHttpResponse(statusCode = 500)
            }
        }) { baseUrl ->
            val result = YouTubeDirectUploadWorker.queryUploadStatus(
                client = client,
                uploadUrl = "$baseUrl/upload/session",
                accessToken = "stale_tok",
                totalBytes = 4096L,
                refreshTokenFn = { "fresh_tok" }
            )
            assertEquals(YouTubeDirectUploadWorker.UploadProbeResult.ResumableAt(1024L), result)
            assertEquals(2, attempts.get())
        }
    }

    @Test
    fun reclaimDeletionPendingR2Media_cleansR2ObjectsAndConfirmsDeletion_ignoringActiveItems() {
        val callLog = CopyOnWriteArrayList<String>()
        val r2DeleteSawAuth = AtomicInteger(0)

        withHttpServer({ _, req ->
            callLog.add("R2 ${req.method} ${req.fullTarget}")
            if (req.header("Authorization") != null) {
                r2DeleteSawAuth.incrementAndGet()
            }
            if (req.method == "DELETE" && req.path == "/r2/dp-with-url-1") {
                StubHttpResponse(204)
            } else {
                StubHttpResponse(404)
            }
        }) { r2BaseUrl ->
            withHttpServer({ _, req ->
                callLog.add("${req.method} ${req.fullTarget}")
                when {
                    req.method == "GET" && req.path == "/media" -> {
                        val payload = """
                            {
                              "items": [
                                { "id": "active-keep-1", "status": "active", "byte_count": 9999 },
                                { "id": "dp-with-url-1", "status": "deletion_pending", "delete_url": "$r2BaseUrl/r2/dp-with-url-1", "final_key": "final/u1/dp-with-url-1" },
                                { "id": "dp-worker-2", "status": "deletion_pending", "final_key": "final/u1/dp-worker-2" }
                              ]
                            }
                        """.trimIndent()
                        StubHttpResponse(200, mapOf("Content-Type" to "application/json"), payload)
                    }
                    req.method == "POST" && req.path == "/media/dp-with-url-1/confirm-deletion" -> {
                        StubHttpResponse(200, mapOf("Content-Type" to "application/json"), """{"status":"deleted"}""")
                    }
                    req.method == "POST" && req.path == "/media/dp-worker-2/confirm-deletion" -> {
                        StubHttpResponse(200, mapOf("Content-Type" to "application/json"), """{"status":"deleted"}""")
                    }
                    else -> StubHttpResponse(404)
                }
            }) { workerBaseUrl ->
                val summary = YouTubeDirectUploadWorker.reclaimDeletionPendingR2Media(
                    client = client,
                    workerUrl = workerBaseUrl,
                    cloudAccessToken = "jwt_cloud_tok"
                )

                assertEquals(2, summary.discoveredCount)
                assertEquals(2, summary.reclaimedCount)
                assertEquals(0, summary.failedCount)
                assertEquals(listOf("dp-with-url-1", "dp-worker-2"), summary.reclaimedIds)
                assertFalse(callLog.any { it.contains("active-keep-1") })
                assertEquals(0, r2DeleteSawAuth.get())

                val deleteIdx = callLog.indexOf("R2 DELETE /r2/dp-with-url-1")
                val confirmIdx = callLog.indexOf("POST /media/dp-with-url-1/confirm-deletion")
                assertTrue("Expected R2 DELETE before /confirm-deletion, log=$callLog", deleteIdx in 0 until confirmIdx)
            }
        }
    }

    @Test
    fun reclaimDeletionPendingR2Media_abortsConfirmationWhenR2DeleteFails() {
        val confirmCalls = AtomicInteger(0)
        withHttpServer({ port, req ->
            when {
                req.method == "GET" && req.path == "/media" -> {
                    val base = "http://127.0.0.1:$port"
                    val payload = """
                        {
                          "deletion_pending": [
                            { "id": "dp-fail-1", "delete_url": "$base/r2/dp-fail-1" }
                          ]
                        }
                    """.trimIndent()
                    StubHttpResponse(200, mapOf("Content-Type" to "application/json"), payload)
                }
                req.method == "DELETE" && req.path == "/r2/dp-fail-1" -> StubHttpResponse(503)
                req.method == "POST" && req.path.endsWith("/confirm-deletion") -> {
                    confirmCalls.incrementAndGet()
                    StubHttpResponse(200)
                }
                else -> StubHttpResponse(404)
            }
        }) { baseUrl ->
            val summary = YouTubeDirectUploadWorker.reclaimDeletionPendingR2Media(
                client = client,
                workerUrl = baseUrl,
                cloudAccessToken = "jwt_cloud_tok"
            )
            assertEquals(1, summary.discoveredCount)
            assertEquals(0, summary.reclaimedCount)
            assertEquals(1, summary.failedCount)
            assertEquals(0, confirmCalls.get())
        }
    }

    @Test
    fun reclaimDeletionPendingR2Media_fallsBackToLegacyPostDeleteWhenConfirmReturns404() {
        val fallbackCalled = AtomicInteger(0)
        withHttpServer({ _, req ->
            when {
                req.method == "GET" && req.path == "/media" -> {
                    StubHttpResponse(
                        200,
                        mapOf("Content-Type" to "application/json"),
                        """{ "items": [ { "id": "dp-legacy-1", "status": "deletion_pending" } ] }"""
                    )
                }
                req.method == "POST" && req.path == "/media/dp-legacy-1/confirm-deletion" -> {
                    StubHttpResponse(404)
                }
                req.method == "POST" && req.path == "/delete" -> {
                    assertTrue(req.body.contains("dp-legacy-1"))
                    fallbackCalled.incrementAndGet()
                    StubHttpResponse(200, mapOf("Content-Type" to "application/json"), """{"status":"deleted"}""")
                }
                else -> StubHttpResponse(404)
            }
        }) { baseUrl ->
            val summary = YouTubeDirectUploadWorker.reclaimDeletionPendingR2Media(
                client = client,
                workerUrl = baseUrl,
                cloudAccessToken = "jwt_cloud_tok"
            )
            assertEquals(1, summary.reclaimedCount)
            assertEquals(1, fallbackCalled.get())
        }
    }

    @Test
    fun extractMediaObjectIdFromR2Key_parsesUuids() {
        val uuid = "123e4567-e89b-12d3-a456-426614174000"
        assertEquals(uuid, YouTubeDirectUploadWorker.extractMediaObjectIdFromR2Key("final/user-1/$uuid"))
        assertEquals(uuid, YouTubeDirectUploadWorker.extractMediaObjectIdFromR2Key("staging/user-1/$uuid"))
        assertEquals(uuid, YouTubeDirectUploadWorker.extractMediaObjectIdFromR2Key(uuid))
        assertNull(YouTubeDirectUploadWorker.extractMediaObjectIdFromR2Key("not-a-uuid"))
        assertNull(YouTubeDirectUploadWorker.extractMediaObjectIdFromR2Key(null))
    }

    @Test
    fun sharedClient_isProcessWideSingleton_withUploadTimeouts() {
        // Every worker instance must reuse the shared client (fresh clients per
        // worker run leaked connection pools/dispatcher threads and paid new
        // TCP+TLS handshakes).
        val first = YouTubeDirectUploadWorker.sharedClient
        val second = YouTubeDirectUploadWorker.sharedClient
        assertSame(first, second)
        assertEquals(30_000, first.connectTimeoutMillis)
        assertEquals(300_000, first.writeTimeoutMillis)
        assertEquals(300_000, first.readTimeoutMillis)
    }

    @Test
    fun reusedChunkBuffer_bodyWritesOnlyTheExplicitChunkLength() {
        // Regression for the per-slice ByteArray(chunkLen) allocation: the
        // hoisted 8 MB buffer is reused across slices, and the request body
        // must serialize exactly buffer[0, chunkLen) with Content-Length ==
        // chunkLen — the final partial chunk must not over-send or copy.
        val buffer = ByteArray(64) { it.toByte() }
        val chunkLen = 17
        val body = buffer.toRequestBody("video/mp4".toMediaType(), 0, chunkLen)

        assertEquals(chunkLen.toLong(), body.contentLength())
        val sink = Buffer()
        body.writeTo(sink)
        assertArrayEquals(buffer.copyOfRange(0, chunkLen), sink.readByteArray())
    }
}
