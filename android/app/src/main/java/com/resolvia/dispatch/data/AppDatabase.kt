package com.resolvia.dispatch.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [SessionEntity::class, SegmentEntity::class, OutboxEntity::class],
    version = 5,
    exportSchema = false
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

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "dispatch_mobile.db"
                )
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_2_4, MIGRATION_4_5)
                .fallbackToDestructiveMigrationOnDowngrade()
                .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING) // Strict WAL mode for crash recovery!
                .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
