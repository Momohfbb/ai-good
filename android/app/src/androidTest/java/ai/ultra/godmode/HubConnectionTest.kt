package ai.ultra.godmode

import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.web.sugar.Web.onWebView
import androidx.test.espresso.web.webdriver.DriverAtoms.findElement
import androidx.test.espresso.web.webdriver.DriverAtoms.getText
import androidx.test.espresso.web.webdriver.DriverAtoms.webClick
import androidx.test.espresso.web.webdriver.DriverAtoms.webKeys
import androidx.test.espresso.web.webdriver.Locator
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.hamcrest.Matchers.containsString
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Runs on the emulator in CI against a real brain started on the build machine
 * (reachable from the emulator as 10.0.2.2). Account "ci-user" is created by the CI script.
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
        ActivityScenario.launch<MainActivity>(intent).use {
            waitFor { onWebView().withElement(findElement(Locator.NAME, "username")) }
            onWebView().withElement(findElement(Locator.NAME, "username")).perform(webKeys("ci-user"))
            onWebView().withElement(findElement(Locator.NAME, "password")).perform(webKeys("ci-password-123"))
            onWebView().withElement(findElement(Locator.CSS_SELECTOR, "button.primary")).perform(webClick())
            waitFor { onWebView().withElement(findElement(Locator.ID, "nav")) }
            // Send a command through the shared brain and wait for the verified result.
            onWebView().withElement(findElement(Locator.CSS_SELECTOR, "nav a[data-p='command']")).perform(webClick())
            waitFor { onWebView().withElement(findElement(Locator.ID, "cmd")) }
            onWebView().withElement(findElement(Locator.ID, "cmd")).perform(webKeys("أنشئ مشروع من الهاتف"))
            onWebView().withElement(findElement(Locator.ID, "send")).perform(webClick())
            waitFor {
                onWebView().withElement(findElement(Locator.ID, "chat"))
                    .check(androidx.test.espresso.web.assertion.WebViewAssertions.webMatches(getText(), containsString("أنشأت المشروع")))
            }
        }
    }

    private fun waitFor(timeoutMs: Long = 30000, block: () -> Unit) {
        val end = System.currentTimeMillis() + timeoutMs
        var last: Throwable? = null
        while (System.currentTimeMillis() < end) {
            try { block(); return } catch (t: Throwable) { last = t; Thread.sleep(500) }
        }
        throw AssertionError("timed out", last)
    }
}
