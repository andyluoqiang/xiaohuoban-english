package com.andyluoqin.englishbuddy;

import android.webkit.JavascriptInterface;

/**
 * H5 <-> 原生桥接，注入为 window.Xiaohuoban。
 * H5 侧无桥接对象时自动降级为 HTML5 Audio + speechSynthesis。
 */
public class JsBridge {

    private final MainActivity activity;

    public JsBridge(MainActivity activity) {
        this.activity = activity;
    }

    @JavascriptInterface
    public void play(String url, String title, String sub) {
        activity.nativePlayEpisode(url, title, sub);
    }

    @JavascriptInterface
    public void playLive(String url, String title, String sub) {
        activity.nativePlayLive(url, title, sub);
    }

    @JavascriptInterface
    public void pause() {
        activity.nativePause();
    }

    @JavascriptInterface
    public void resume() {
        activity.nativeResume();
    }

    @JavascriptInterface
    public void stop() {
        activity.nativeStop();
    }

    /** forward: 1 = 快进15秒, 0 = 后退15秒 */
    @JavascriptInterface
    public void skip15(int forward) {
        activity.nativeSkip15(forward != 0);
    }

    /** pct: 0-100 进度条百分比 */
    @JavascriptInterface
    public void seekPercent(int pct) {
        activity.nativeSeekPercent(pct);
    }

    /** 单词发音（系统 TTS，美式英语） */
    @JavascriptInterface
    public void speak(String word) {
        activity.nativeSpeak(word);
    }

    /** 错误页：原生重载主页面（比 location.href 可靠） */
    @JavascriptInterface
    public void reload() {
        activity.nativeReload();
    }

    /** 错误页：系统浏览器打开（对照测试用） */
    @JavascriptInterface
    public void openBrowser() {
        activity.nativeOpenBrowser();
    }
}
