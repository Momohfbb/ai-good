package ai.ultra.godmode

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.webkit.CookieManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit
import org.json.JSONArray

/** Every 15 minutes (Android's minimum) asks the brain for tasks that need approval or failed. */
class TaskAlertWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val hub = Prefs.hub(applicationContext) ?: return Result.success()
        val cookie = CookieManager.getInstance().getCookie(hub) ?: return Result.success()
        val seen = Prefs.seen(applicationContext).toMutableSet()
        for ((status, title) in listOf("needs_approval" to R.string.notif_approval, "failed" to R.string.notif_failed)) {
            val tasks = try { fetch("${hub}api/tasks?limit=20&status=$status", cookie) } catch (e: Exception) { return Result.retry() }
            for (i in 0 until tasks.length()) {
                val t = tasks.getJSONObject(i)
                val key = t.getString("id") + ":" + status
                if (seen.add(key)) notify(t.getString("id").hashCode(), applicationContext.getString(title), t.getString("title"))
            }
        }
        Prefs.setSeen(applicationContext, seen.toList().takeLast(300).toSet())
        return Result.success()
    }

    private fun fetch(url: String, cookie: String): JSONArray {
        val c = URL(url).openConnection() as HttpURLConnection
        c.setRequestProperty("Cookie", cookie)
        c.connectTimeout = 8000
        c.readTimeout = 8000
        try {
            if (c.responseCode != 200) throw IllegalStateException("HTTP ${c.responseCode}")
            return JSONArray(c.inputStream.bufferedReader().readText())
        } finally { c.disconnect() }
    }

    private fun notify(id: Int, title: String, text: String) {
        val nm = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, applicationContext.getString(R.string.channel_name),
                NotificationManager.IMPORTANCE_DEFAULT))
        }
        val open = PendingIntent.getActivity(applicationContext, 0,
            Intent(applicationContext, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(title).setContentText(text).setContentIntent(open).setAutoCancel(true).build()
        try { NotificationManagerCompat.from(applicationContext).notify(id, n) } catch (_: SecurityException) {}
    }

    companion object {
        private const val CHANNEL = "tasks"
        fun schedule(c: Context) {
            WorkManager.getInstance(c).enqueueUniquePeriodicWork("task-alerts", ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<TaskAlertWorker>(15, TimeUnit.MINUTES).build())
        }
    }
}
