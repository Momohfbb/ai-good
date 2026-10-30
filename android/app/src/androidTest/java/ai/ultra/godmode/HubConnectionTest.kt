package ai.ultra.godmode

import android.content.Intent
import android.webkit.WebView
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Runs on the emulator in CI against a real brain started on the build machine
 * (reachable from the emulator as 10.0.2.2). Account "ci-user" is created by the CI script.
 * The page is driven with plain JavaScript inside the app's WebView, like a user typing and tapping.
 */
@RunWith(AndroidJUnit4::class)
class HubConnectionTest {
    private val hub = InstrumentationRegistry.getArguments().getString("hub_url") ?: "http://10.0.2.2:8765/"

    @Test
    fun brainIsReachable() {
        assertTrue("brain health check failed at $hub", MainActivity.reachable(MainActivity.normalise(hub)))
    }

    @Test
    fun loginAndSendCommandFromPhone() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val intent = Intent(ctx, MainActivity::class.java).putExtra(MainActivity.EXTRA_HUB_URL, hub)
        ActivityScenario.launch<MainActivity>(intent).use { scenario ->
            val js = { script: String -> eval(scenario, script) }

            waitUntil("login form") { js("!!document.querySelector('input[name=password]')") == "true" }
            js("""(function(){
                const f = document.querySelector('input[name=password]').form;
                f.querySelector('input[name=username]').value = 'ci-user';
                f.querySelector('input[name=password]').value = 'ci-password-123';
                f.requestSubmit(); return 1; })()""")

            waitUntil("dashboard after login") { js("!!document.querySelector('#nav a[data-p=command]')") == "true" }
            js("document.querySelector('#nav a[data-p=command]').click()")

            waitUntil("command page") { js("!!document.getElementById('cmd')") == "true" }
            js("""(function(){
                document.getElementById('cmd').value = 'أنشئ مشروع من الهاتف';
                document.getElementById('send').click(); return 1; })()""")

            // The brain plans, executes and verifies the task; the phone shows the verified result.
            waitUntil("verified result in chat", 60000) {
                js("document.body.innerText.indexOf('أنشأت المشروع') >= 0") == "true"
            }
        }
    }

    private fun eval(scenario: ActivityScenario<MainActivity>, script: String): String? {
        val latch = CountDownLatch(1)
        var result: String? = null
        scenario.onActivity { activity ->
            activity.findViewById<WebView>(R.id.web).evaluateJavascript(script) { result = it; latch.countDown() }
        }
        latch.await(10, TimeUnit.SECONDS)
        return result
    }

    private fun waitUntil(what: String, timeoutMs: Long = 30000, check: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            if (runCatching(check).getOrDefault(false)) return
            Thread.sleep(500)
        }
        throw AssertionError("timed out waiting for $what")
    }
}
