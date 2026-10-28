package com.resolvia.dispatch.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [SessionEntity::class, SegmentEntity::class, OutboxEntity::class],
    version = 6,
    exportSchema = true
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun recordingDao(): RecordingDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        private fun ensureColumnExists(db: SupportSQLiteDatabase, table: String, column: String, definition: String) {
            val cursor = db.query("PRAGMA table_info($table)")
            var exists = false
            try {
                val nameIdx = cursor.getColumnIndex("name")
                while (cursor.moveToNext()) {
                    if (nameIdx != -1 && cursor.getString(nameIdx).equals(column, ignoreCase = true)) {
                        exists = true
                        break
                    }
                }
            } finally {
                cursor.close()
            }
            if (!exists) {
                db.execSQL("ALTER TABLE $table ADD COLUMN $column $definition")
            }
        }

        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                ensureColumnExists(db, "segments", "youtubeVideoId", "TEXT DEFAULT NULL")
            }
        }

        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                ensureColumnExists(db, "sessions", "userId", "TEXT DEFAULT NULL")
                ensureColumnExists(db, "segments", "userId", "TEXT DEFAULT NULL")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_sessions_userId ON sessions(userId)")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_segments_userId ON segments(userId)")
            }
        }

        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                ensureColumnExists(db, "sessions", "userId", "TEXT DEFAULT NULL")
                ensureColumnExists(db, "segments", "userId", "TEXT DEFAULT NULL")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_sessions_userId ON sessions(userId)")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_segments_userId ON segments(userId)")
            }
        }

        val MIGRATION_2_4 = object : Migration(2, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                ensureColumnExists(db, "sessions", "userId", "TEXT DEFAULT NULL")
                ensureColumnExists(db, "segments", "userId", "TEXT DEFAULT NULL")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_sessions_userId ON sessions(userId)")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_segments_userId ON segments(userId)")
            }
        }

        val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Upload state machine hardening: which credential owns the video
                // plus bounded YouTube processing-check bookkeeping.
                ensureColumnExists(db, "segments", "uploadOrigin", "TEXT DEFAULT ''")
                ensureColumnExists(db, "segments", "youtubeCheckAttempts", "INTEGER DEFAULT 0")
                ensureColumnExists(db, "segments", "youtubeCheckAt", "INTEGER DEFAULT 0")
            }
        }

        private fun rebuildTable(
            db: SupportSQLiteDatabase,
            table: String,
            tempTable: String,
            createTempSql: String,
            insertSelectSql: String,
            indices: Array<String>,
        ) {
            db.execSQL("DROP TABLE IF EXISTS `$tempTable`")
            db.execSQL(createTempSql)
            db.execSQL(insertSelectSql)
            db.execSQL("DROP TABLE IF EXISTS `$table`")
            db.execSQL("ALTER TABLE `$tempTable` RENAME TO `$table`")
            indices.forEach { db.execSQL(it) }
        }

        val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Room validates the post-migration schema against the COMPILED entity
                // (TableInfo.hasEqualColumnsAndConstraints). The hand-written
                // `ALTER TABLE … ADD COLUMN … DEFAULT …` statements in migrations 1..5
                // produce columns whose notNull/default differ from what the entities
                // declare: `uploadOrigin TEXT DEFAULT ''` (nullable, default '') where
                // Room expects `uploadOrigin TEXT NOT NULL` with no default, and
                // `uploadLeaseAt INTEGER DEFAULT NULL` where Room expects a plain
                // nullable INTEGER. Any mismatch throws
                // "Migration didn't properly handle: segments", Room rolls the whole
                // upgrade back, and the database then fails to open on EVERY launch —
                // which bricks recording and the recordings list.
                //
                // Rebuild to the compiled schema so drifted (upgraded) and fresh
                // databases converge. Rows are preserved; NOT NULL columns are
                // backfilled with COALESCE.
                rebuildTable(
                    db = db,
                    table = "segments",
                    tempTable = "segments_v6_rebuild",
                    createTempSql = "CREATE TABLE `segments_v6_rebuild` (" +
                        "`segmentId` TEXT NOT NULL, `sessionId` TEXT NOT NULL, " +
                        "`sequenceNumber` INTEGER NOT NULL, `filename` TEXT NOT NULL, " +
                        "`filepath` TEXT NOT NULL, `fileSizeBytes` INTEGER NOT NULL, " +
                        "`sha256Hash` TEXT NOT NULL, `status` TEXT NOT NULL, " +
                        "`createdAt` INTEGER NOT NULL, `finalizedAt` INTEGER, " +
                        "`youtubeVideoId` TEXT, `userId` TEXT, `uploadOrigin` TEXT NOT NULL, " +
                        "`youtubeCheckAttempts` INTEGER NOT NULL, `youtubeCheckAt` INTEGER NOT NULL, " +
                        "`uploadLeaseAt` INTEGER, PRIMARY KEY(`segmentId`))",
                    insertSelectSql = "INSERT INTO `segments_v6_rebuild` (`segmentId`, `sessionId`, " +
                        "`sequenceNumber`, `filename`, `filepath`, `fileSizeBytes`, `sha256Hash`, `status`, " +
                        "`createdAt`, `finalizedAt`, `youtubeVideoId`, `userId`, `uploadOrigin`, " +
                        "`youtubeCheckAttempts`, `youtubeCheckAt`, `uploadLeaseAt`) " +
                        "SELECT `segmentId`, `sessionId`, COALESCE(`sequenceNumber`, 0), " +
                        "COALESCE(`filename`, ''), COALESCE(`filepath`, ''), COALESCE(`fileSizeBytes`, 0), " +
                        "COALESCE(`sha256Hash`, ''), COALESCE(`status`, 'RECORDING'), COALESCE(`createdAt`, 0), " +
                        "`finalizedAt`, `youtubeVideoId`, `userId`, COALESCE(`uploadOrigin`, ''), " +
                        "COALESCE(`youtubeCheckAttempts`, 0), COALESCE(`youtubeCheckAt`, 0), NULL " +
                        "FROM `segments`",
                    indices = arrayOf(
                        "CREATE INDEX IF NOT EXISTS `index_segments_userId` ON `segments` (`userId`)"
                    )
                )
                rebuildTable(
                    db = db,
                    table = "sessions",
                    tempTable = "sessions_v6_rebuild",
                    createTempSql = "CREATE TABLE `sessions_v6_rebuild` (" +
                        "`sessionId` TEXT NOT NULL, `startedAt` INTEGER NOT NULL, " +
                        "`totalSegments` INTEGER NOT NULL, `status` TEXT NOT NULL, " +
                        "`notes` TEXT NOT NULL, `userId` TEXT, PRIMARY KEY(`sessionId`))",
                    insertSelectSql = "INSERT INTO `sessions_v6_rebuild` (`sessionId`, `startedAt`, " +
                        "`totalSegments`, `status`, `notes`, `userId`) " +
                        "SELECT `sessionId`, COALESCE(`startedAt`, 0), COALESCE(`totalSegments`, 0), " +
                        "COALESCE(`status`, 'RECORDING'), COALESCE(`notes`, ''), `userId` FROM `sessions`",
                    indices = arrayOf(
                        "CREATE INDEX IF NOT EXISTS `index_sessions_userId` ON `sessions` (`userId`)"
                    )
                )
            }
        }

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "dispatch_mobile.db"
                )
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_2_4, MIGRATION_4_5, MIGRATION_5_6)
                .fallbackToDestructiveMigrationOnDowngrade()
                .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING) // Strict WAL mode for crash recovery!
                .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
