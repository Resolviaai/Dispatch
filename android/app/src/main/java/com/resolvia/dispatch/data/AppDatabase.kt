package com.resolvia.dispatch.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [SessionEntity::class, SegmentEntity::class, OutboxEntity::class],
    version = 3,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun recordingDao(): RecordingDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE segments ADD COLUMN youtubeVideoId TEXT DEFAULT NULL")
            }
        }

        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE sessions ADD COLUMN userId TEXT DEFAULT NULL")
                db.execSQL("ALTER TABLE segments ADD COLUMN userId TEXT DEFAULT NULL")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_sessions_userId ON sessions(userId)")
                db.execSQL("CREATE INDEX IF NOT EXISTS index_segments_userId ON segments(userId)")
            }
        }

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "dispatch_mobile.db"
                )
                .addMigrations(MIGRATION_1_2, MIGRATION_2_3)
                .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING) // Strict WAL mode for crash recovery!
                .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
