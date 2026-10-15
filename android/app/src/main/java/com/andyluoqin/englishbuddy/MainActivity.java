package com.andyluoqin.englishbuddy;

import android.annotation.SuppressLint;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.ActivityInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.animation.AlphaAnimation;
import android.view.animation.Animation;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import java.util.Locale;

/**
 * 主界面：WebView 承载 H5 应用，通过 JS Bridge 调用原生能力。
 */
public class MainActivity extends AppCompatActivity {

    public static final String LOAD_URL = "https://www.jxonline.top/app/index.html";
    private static final String HOST_ALLOW = "www.jxonline.top";

    private WebView web;
    private View splash;
    private PlaybackService svc;
    private boolean svcBound = false;
    private Runnable pendingPlay = null; // 服务尚未连接时排队的播放请求
    private TextToSpeech tts;
    private boolean ttsReady = false;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private long splashShownAt = 0L;
    private boolean pageLoaded = false;
    private int loadRetries = 0;
    private static final int MAX_LOAD_RETRIES = 3;

    // 视频全屏（HTML5 video 全屏按钮 → onShowCustomView）
    private View customView = null;
    private WebChromeClient.CustomViewCallback customViewCallback = null;

    private final ServiceConnection conn = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            svc = ((PlaybackService.LocalBinder) service).getService();
            svc.setListener(new PlaybackService.PlaybackListener() {
                @Override
                public void onProgress(final long cur, final long dur) {
                    // ExoPlayer 位置/时长单位为毫秒，H5 的 fmtTime 按秒格式化 —— 转换为秒后再上报
                    long cs = Math.round(cur / 1000.0);
                    long ds = Math.round(dur / 1000.0);
                    callJs(String.format("window.onNativeProgress(%d,%d)", cs, ds));
                }

                @Override
                public void onStateChanged(final boolean playing) {
                    callJs("window.onNativeState(" + playing + ")");
                }

                @Override
                public void onEnded() {
                    callJs("window.onNativeEnded()");
                }
            });
            // 服务连接就绪后，补发此前排队的播放请求（消除冷启动竞态）
            if (pendingPlay != null) {
                final Runnable r = pendingPlay;
                pendingPlay = null;
                handler.post(r);
            }
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            svc = null;
            svcBound = false;
        }
    };

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        splash = findViewById(R.id.splash);
        splashShownAt = System.currentTimeMillis();

        web = findViewById(R.id.webview);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);                 // localStorage 存收藏
        s.setMediaPlaybackRequiresUserGesture(false); // 程序化播放音频
        s.setSupportZoom(false);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE); // 电台 http 流

        web.addJavascriptInterface(new JsBridge(this), "Xiaohuoban");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                String host = request.getUrl().getHost();
                if (host != null && host.equalsIgnoreCase(HOST_ALLOW)) {
                    return false; // 站内导航留在 App 内
                }
                try {
                    Intent i = new Intent(Intent.ACTION_VIEW, request.getUrl());
                    startActivity(i);
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (LOAD_URL.equals(url) || url.contains("jxonline.top")) {
                    pageLoaded = true;
                    loadRetries = 0;
                }
                dismissSplash();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    String desc = "";
                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M) {
                        try {
                            CharSequence d = error.getDescription();
                            desc = d == null ? "" : d.toString();
                        } catch (Exception ignored) {
                        }
                    }
                    if (loadRetries < MAX_LOAD_RETRIES) {
                        loadRetries++;
                        final int n = loadRetries;
                        handler.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                if (!pageLoaded) {
                                    android.util.Log.w("EnglishBuddy", "retry load " + n + "/" + MAX_LOAD_RETRIES);
                                    web.loadUrl(LOAD_URL);
                                }
                            }
                        }, 2000);
                    } else {
                        showErrorPage(desc);
                    }
                }
            }

            @Override
            public void onReceivedSslError(WebView view, android.webkit.SslErrorHandler handler, android.net.http.SslError error) {
                handler.cancel();
                showErrorPage("ERR_CERT code=" + (error == null ? "?" : String.valueOf(error.getPrimaryError())));
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage cm) {
                String msg = cm.message();
                if (msg != null && (msg.contains("Error") || msg.contains("error") || msg.contains("Uncaught"))) {
                    android.util.Log.w("H5Console", cm.message() + " @" + cm.lineNumber());
                }
                return true;
            }

            /* ---------- HTML5 video 全屏：横屏 + 沉浸式 ---------- */
            @Override
            public void onShowCustomView(View view, WebChromeClient.CustomViewCallback callback) {
                if (customView != null) {
                    // 已有全屏视图在展示：直接拒绝新请求
                    callback.onCustomViewHidden();
                    return;
                }
                customView = view;
                customViewCallback = callback;
                web.setVisibility(View.GONE);
                // 横屏（跟随传感器，允许左右两个方向）
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
                // 沉浸式：隐藏状态栏/导航栏
                getWindow().getDecorView().setSystemUiVisibility(
                        View.SYSTEM_UI_FLAG_FULLSCREEN
                                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
                // 全屏期间保持屏幕常亮
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                ViewGroup root = (ViewGroup) web.getParent();
                view.setBackgroundColor(0xFF000000);
                root.addView(view, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER));
            }

            @Override
            public void onHideCustomView() {
                exitFullscreen();
            }
        });

        web.loadUrl(LOAD_URL);

        tts = new TextToSpeech(this, new TextToSpeech.OnInitListener() {
            @Override
            public void onInit(int status) {
                if (status == TextToSpeech.SUCCESS) {
                    // 部分设备只有英文/默认语音数据，逐个尝试，避免误判“未安装”
                    boolean ok = false;
                    int r1 = tts.setLanguage(Locale.US);
                    if (!(r1 == TextToSpeech.LANG_MISSING_DATA || r1 == TextToSpeech.LANG_NOT_SUPPORTED)) {
                        ok = true;
                    } else {
                        int r2 = tts.setLanguage(Locale.ENGLISH);
                        ok = !(r2 == TextToSpeech.LANG_MISSING_DATA || r2 == TextToSpeech.LANG_NOT_SUPPORTED);
                    }
                    ttsReady = ok;
                    // 即使原生 TTS 缺英文语音数据也不提示：nativeSpeak 会自动降级到 WebView 的 speechSynthesis
                } else {
                    ttsReady = false;
                }
            }
        });

        // 兜底：15 秒后无论如何隐藏启动页
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                forceDismissSplash();
            }
        }, 15000);
    }

    private void dismissSplash() {
        pageLoaded = true;
        long elapsed = System.currentTimeMillis() - splashShownAt;
        long wait = Math.max(0, 800 - elapsed);
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                forceDismissSplash();
            }
        }, wait);
    }

    private void forceDismissSplash() {
        if (splash != null && splash.getVisibility() == View.VISIBLE) {
            AlphaAnimation fade = new AlphaAnimation(1f, 0f);
            fade.setDuration(280);
            fade.setAnimationListener(new Animation.AnimationListener() {
                @Override
                public void onAnimationStart(Animation animation) {
                }

                @Override
                public void onAnimationEnd(Animation animation) {
                    splash.setVisibility(View.GONE);
                }

                @Override
                public void onAnimationRepeat(Animation animation) {
                }
            });
            splash.startAnimation(fade);
        }
    }

    private void showErrorPage(String errMsg) {
        String safe = errMsg == null ? "" : errMsg
                .replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&#39;");
        String html = "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>body{font-family:sans-serif;background:#0d1b33;color:#fff;text-align:center;padding-top:32%;}"
                + "button{background:#f59f00;color:#0d1b33;border:none;border-radius:18px;padding:12px 30px;font-size:15px;margin-top:24px;font-weight:bold;}"
                + ".sub{opacity:.85;font-size:12.5px;margin-top:18px;word-break:break-all;padding:0 24px;line-height:1.7}"
                + ".btn2{background:transparent;border:1.5px solid #5a89c9;color:#a9c4e8;margin-top:14px;font-weight:normal}</style>"
                + "</head><body><div style='font-size:46px'>📡</div><h3>连接不上服务器</h3>"
                + "<p style='opacity:.7;font-size:13px'>已自动重试 " + MAX_LOAD_RETRIES + " 次，仍无法访问</p>"
                + "<div class='sub'>失败原因：<b>" + (safe.isEmpty() ? "未知（网络错误）" : safe) + "</b><br>"
                + "可尝试：切换 Wi-Fi / 蜂窝网络后重试；或用系统浏览器打开对照测试</div>"
                + "<button onclick=\"Xiaohuoban.reload()\">重新加载</button><br>"
                + "<button class='btn2' onclick=\"Xiaohuoban.openBrowser()\">系统浏览器打开</button></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
        dismissSplash();
    }

    /* ---------------- 原生回调转 H5 ---------------- */

    private void callJs(final String js) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (web != null) {
                    web.evaluateJavascript(js == null ? "" : js, null);
                }
            }
        });
    }

    /* ---------------- JS Bridge 暴露给 H5 的实现 ---------------- */

    void nativePlayEpisode(final String url, final String title, final String sub) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                ensureServiceRunning();
                if (svc != null) {
                    svc.playEpisode(url, title, sub);
                } else {
                    pendingPlay = new Runnable() {
                        @Override
                        public void run() {
                            if (svc != null) svc.playEpisode(url, title, sub);
                        }
                    };
                }
            }
        });
    }

    void nativePlayLive(final String url, final String title, final String sub) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                ensureServiceRunning();
                if (svc != null) {
                    svc.playLive(url, title, sub);
                } else {
                    pendingPlay = new Runnable() {
                        @Override
                        public void run() {
                            if (svc != null) svc.playLive(url, title, sub);
                        }
                    };
                }
            }
        });
    }

    void nativePause() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (svc != null) svc.pause();
            }
        });
    }

    void nativeResume() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (svc != null) svc.resume();
            }
        });
    }

    void nativeStop() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (svc != null) svc.stopPlayback();
            }
        });
    }

    void nativeSkip15(final boolean forward) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (svc != null) svc.skip15(forward);
            }
        });
    }

    void nativeSeekPercent(final int pct) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (svc != null) svc.seekPercent(pct);
            }
        });
    }

    void nativeSpeak(final String word) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (ttsReady && tts != null) {
                    tts.setSpeechRate(0.85f);
                    tts.speak(word, TextToSpeech.QUEUE_FLUSH, null, "word_" + word);
                } else if (web != null) {
                    // 降级：使用 WebView 的 speechSynthesis（网页版发音即此方案，已验证可用）
                    String js = "try{window.speechSynthesis.cancel();var u=new SpeechSynthesisUtterance("
                            + jsQuote(word) + ");u.lang='en-US';u.rate=0.85;window.speechSynthesis.speak(u);}catch(e){}";
                    web.evaluateJavascript(js, null);
                }
            }
        });
    }

    private String jsQuote(String s) {
        if (s == null) s = "";
        StringBuilder b = new StringBuilder();
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\') b.append("\\\\");
            else if (c == '"') b.append("\\\"");
            else if (c == '\n') b.append("\\n");
            else if (c == '\r') b.append("\\r");
            else if (c < 0x20) b.append("\\u").append(String.format("%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }

    void nativeReload() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                loadRetries = 0;
                pageLoaded = false;
                if (splash != null) {
                    splash.setVisibility(View.VISIBLE);
                }
                splashShownAt = System.currentTimeMillis();
                web.loadUrl(LOAD_URL);
                handler.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        forceDismissSplash();
                    }
                }, 15000);
            }
        });
    }

    void nativeOpenBrowser() {
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(LOAD_URL));
            startActivity(i);
        } catch (Exception e) {
            Toast.makeText(this, "系统浏览器不可用", Toast.LENGTH_SHORT).show();
        }
    }

    private void ensureServiceRunning() {
        Intent i = new Intent(this, PlaybackService.class);
        // 关键修复：必须以 start 方式拉起服务（转为“已启动”状态）。
        // 仅 bind 的服务在 Activity onStop 解绑后会被系统销毁，导致退后台/锁屏立即停播。
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(i);
        } else {
            startService(i);
        }
        if (!svcBound || svc == null) {
            bindService(i, conn, Context.BIND_AUTO_CREATE);
            svcBound = true;
        }
    }

    /* ---------------- 生命周期 ---------------- */

    @Override
    protected void onStart() {
        super.onStart();
        if (!svcBound) {
            Intent i = new Intent(this, PlaybackService.class);
            bindService(i, conn, Context.BIND_AUTO_CREATE);
            svcBound = true;
        }
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (svcBound) {
            if (svc != null) svc.setListener(null);
            unbindService(conn);
            svcBound = false;
            svc = null;
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        handler.removeCallbacksAndMessages(null);
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        exitFullscreen();
        if (web != null) {
            web.removeJavascriptInterface("Xiaohuoban");
            web.destroy();
            web = null;
        }
    }

    /** 退出视频全屏：移除全屏视图、恢复竖屏与系统栏 */
    private void exitFullscreen() {
        if (customView == null) return;
        ViewGroup root = (ViewGroup) web.getParent();
        root.removeView(customView);
        customView = null;
        if (customViewCallback != null) {
            customViewCallback.onCustomViewHidden();
            customViewCallback = null;
        }
        web.setVisibility(View.VISIBLE);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    /* ---------------- 返回键交给 H5 的视图栈 ---------------- */

    @Override
    public void onBackPressed() {
        // 视频全屏中：返回键优先退出全屏（不退出页面）
        if (customView != null) {
            exitFullscreen();
            return;
        }
        if (web != null) {
            web.evaluateJavascript("(window.nativeBack ? window.nativeBack() : false) === true",
                    new android.webkit.ValueCallback<String>() {
                        @Override
                        public void onReceiveValue(String value) {
                            if (!"true".equals(value)) {
                                moveTaskToBack(true); // 顶级页面：退到后台，不打断播放
                            }
                        }
                    });
        } else {
            moveTaskToBack(true);
        }
    }
}
