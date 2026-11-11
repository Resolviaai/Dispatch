package com.resolvia.dispatch.data

import androidx.sqlite.db.SupportSQLiteDatabase
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.lang.reflect.Proxy

/**
 * Verifies the Room v8 -> v9 schema migration contract and exported 9.json indices.
 */
class AppDatabaseMigrationTest {

    private fun resolveAppFile(relative: String): File {
        val candidates = listOf(
            File(relative),
            File("app/$relative"),
            File("android/app/$relative")
        )
        return candidates.firstOrNull { it.exists() }
            ?: error("Could not locate $relative from cwd=${File(".").absolutePath}")
    }

    private fun resolveSchemaFile(version: Int): File =
        resolveAppFile("schemas/com.resolvia.dispatch.data.AppDatabase/$version.json")

    private fun extractEntityMap(schemaJson: JSONObject): Map<String, JSONObject> {
        val entities = schemaJson.getJSONObject("database").getJSONArray("entities")
        val map = linkedMapOf<String, JSONObject>()
        for (i in 0 until entities.length()) {
            val entity = entities.getJSONObject(i)
            map[entity.getString("tableName")] = entity
        }
        return map
    }

    private fun extractIndicesByName(entity: JSONObject): Map<String, String> {
        val tableName = entity.getString("tableName")
        val indices = entity.getJSONArray("indices")
        val result = linkedMapOf<String, String>()
        for (i in 0 until indices.length()) {
            val idx = indices.getJSONObject(i)
            val name = idx.getString("name")
            val createSql = idx.getString("createSql").replace("\${TABLE_NAME}", tableName)
            result[name] = createSql
        }
        return result
    }

    @Test
    fun appDatabaseDeclaresVersion9ExportSchemaAndEntities() {
        val appDbSource = resolveAppFile("src/main/java/com/resolvia/dispatch/data/AppDatabase.kt").readText()
        assertTrue(
            "AppDatabase must declare version = 9",
            Regex("""version\s*=\s*9\b""").containsMatchIn(appDbSource)
        )
        assertTrue(
            "AppDatabase must declare exportSchema = true",
            Regex("""exportSchema\s*=\s*true\b""").containsMatchIn(appDbSource)
        )
        assertTrue(appDbSource.contains("SegmentEntity::class"))
        assertTrue(appDbSource.contains("OutboxEntity::class"))

        val segmentSource = resolveAppFile("src/main/java/com/resolvia/dispatch/data/SegmentEntity.kt").readText()
        assertTrue(segmentSource.contains("\"sessionId\", \"status\""))
        assertTrue(segmentSource.contains("\"status\", \"sequenceNumber\""))

        val outboxSource = resolveAppFile("src/main/java/com/resolvia/dispatch/data/OutboxEntity.kt").readText()
        assertTrue(outboxSource.contains("\"status\", \"updatedAt\""))
        assertTrue(outboxSource.contains("\"sessionId\""))
    }

    @Test
    fun migration8To9CreatesExactIndexesDeclaredInExportedSchema9() {
        val migration = AppDatabase.MIGRATION_8_9
        assertEquals(8, migration.startVersion)
        assertEquals(9, migration.endVersion)

        val executedSql = mutableListOf<String>()
        val recordingDb = Proxy.newProxyInstance(
            SupportSQLiteDatabase::class.java.classLoader,
            arrayOf(SupportSQLiteDatabase::class.java)
        ) { _, method, args ->
            if (method.name == "execSQL" && args != null && args.isNotEmpty()) {
                executedSql.add(args[0] as String)
                return@newProxyInstance null
            }
            when (method.returnType) {
                Boolean::class.javaPrimitiveType -> false
                Int::class.javaPrimitiveType -> 0
                Long::class.javaPrimitiveType -> 0L
                else -> null
            }
        } as SupportSQLiteDatabase

        migration.migrate(recordingDb)

        val schema8 = JSONObject(resolveSchemaFile(8).readText())
        val schema9 = JSONObject(resolveSchemaFile(9).readText())
        assertEquals(8, schema8.getJSONObject("database").getInt("version"))
        assertEquals(9, schema9.getJSONObject("database").getInt("version"))

        val entities8 = extractEntityMap(schema8)
        val entities9 = extractEntityMap(schema9)
        assertEquals(entities8.keys, entities9.keys)

        // Ensure v8 -> v9 changed only indices (no unexpected column/createSql drift)
        for (table in entities9.keys) {
            assertEquals(
                "Table $table createSql drifted unexpectedly in v9",
                entities8.getValue(table).getString("createSql"),
                entities9.getValue(table).getString("createSql")
            )
        }

        val expectedNewIndices = linkedMapOf<String, String>()
        for (table in listOf("segments", "outbox")) {
            val oldIdx = extractIndicesByName(entities8.getValue(table))
            val newIdx = extractIndicesByName(entities9.getValue(table))
            for ((name, sql) in newIdx) {
                if (!oldIdx.containsKey(name)) {
                    expectedNewIndices[name] = sql
                }
            }
        }

        assertEquals(
            setOf(
                "index_segments_sessionId_status",
                "index_segments_status_sequenceNumber",
                "index_outbox_status_updatedAt",
                "index_outbox_sessionId"
            ),
            expectedNewIndices.keys
        )

        val normalizedExecuted = executedSql.map { it.trim().replace(Regex("\\s+"), " ") }.toSet()
        for ((indexName, expectedCreateSql) in expectedNewIndices) {
            val normalizedExpected = expectedCreateSql.trim().replace(Regex("\\s+"), " ")
            assertTrue(
                "MIGRATION_8_9 did not emit expected SQL for $indexName.\nExpected: $normalizedExpected\nActual: $normalizedExecuted",
                normalizedExecuted.contains(normalizedExpected)
            )
        }
        assertEquals(expectedNewIndices.size, executedSql.size)
    }

    @Test
    fun appDatabaseSourceRegistersMigration8To9() {
        val text = resolveAppFile("src/main/java/com/resolvia/dispatch/data/AppDatabase.kt").readText()
        assertTrue(
            "AppDatabase.getDatabase() must register MIGRATION_8_9 in addMigrations(...)",
            text.contains("MIGRATION_7_8, MIGRATION_8_9") ||
                Regex("""\.addMigrations\([^)]*\bMIGRATION_8_9\b""").containsMatchIn(text)
        )
    }
}
