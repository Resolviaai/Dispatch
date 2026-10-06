package com.resolvia.dispatch.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [SessionEntity::class, SegmentEntity::class, OutboxEntity::class],
    version = 1,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun recordingDao(): RecordingDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "dispatch_mobile.db"
                )
                .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING) // Strict WAL mode for crash recovery!
                .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
