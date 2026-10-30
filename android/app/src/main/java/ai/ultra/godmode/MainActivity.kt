package ai.ultra.godmode

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * The Android app is a client of the central brain running on the PC (or a server):
 * same account, same tasks, memory and settings. It shows the shared dashboard and
 * raises notifications when a task needs approval or fails.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private lateinit var setup: LinearLayout
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private val pickFile = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        fileCallback?.onReceiveValue(if (uri != null) arrayOf(uri) else null)
        fileCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        web = findViewById(R.id.web)
        setup = findViewById(R.id.setup)
        configureWebView()

        // A hub address can be passed by the automated tests or a shortcut.
        intent.getStringExtra(EXTRA_HUB_URL)?.let { Prefs.setHub(this, it) }

        val hub = Prefs.hub(this)
        if (hub.isNullOrBlank()) showSetup() else openHub(hub)

        findViewById<Button>(R.id.connect).setOnClickListener {
            val url = normalise(findViewById<EditText>(R.id.hubUrl).text.toString())
            val status = findViewById<TextView>(R.id.status)
            status.text = getString(R.string.checking)
            thread {
                val ok = reachable(url)
                runOnUiThread {
                    if (ok) {
                        Prefs.setHub(this, url)
                        openHub(url)
                    } else status.text = getString(R.string.unreachable)
                }
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.visibility == View.VISIBLE && web.canGoBack()) web.goBack() else finish()
            }
        })

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) {}.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        TaskAlertWorker.schedule(this)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            userAgentString = "$userAgentString UltraAI-Android/${BuildConfig.VERSION_NAME}"
        }
        CookieManager.getInstance().setAcceptCookie(true)
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Stay inside the brain; open anything else in the normal browser.
                val hub = Prefs.hub(this@MainActivity) ?: return false
                if (request.url.toString().startsWith(hub)) return false
                startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, request.url))
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                CookieManager.getInstance().flush()
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                pickFile.launch("*/*")
                return true
            }
        }
    }

    private fun showSetup() {
        web.visibility = View.GONE
        setup.visibility = View.VISIBLE
        findViewById<EditText>(R.id.hubUrl).setText(Prefs.hub(this) ?: "http://192.168.1.10:8765")
    }

    private fun openHub(url: String) {
        setup.visibility = View.GONE
        web.visibility = View.VISIBLE
        web.loadUrl(url)
    }

    companion object {
        const val EXTRA_HUB_URL = "hub_url"

        fun normalise(raw: String): String {
            var u = raw.trim().trimEnd('/')
            if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
            return "$u/"
        }

        fun reachable(base: String): Boolean = try {
            val c = URL(base + "api/health").openConnection() as HttpURLConnection
            c.connectTimeout = 4000
            c.readTimeout = 4000
            val ok = c.responseCode == 200 && c.inputStream.bufferedReader().readText().contains("\"ok\"")
            c.disconnect()
            ok
        } catch (e: Exception) {
            false
        }
    }
}

object Prefs {
    private fun p(c: Context) = c.getSharedPreferences("ultra", Context.MODE_PRIVATE)
    fun hub(c: Context): String? = p(c).getString("hub", null)
    fun setHub(c: Context, url: String) = p(c).edit().putString("hub", MainActivity.normalise(url)).apply()
    fun seen(c: Context): Set<String> = p(c).getStringSet("seen", emptySet()) ?: emptySet()
    fun setSeen(c: Context, s: Set<String>) = p(c).edit().putStringSet("seen", s).apply()
}
