package com.andyluoqin.englishbuddy;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.support.v4.media.MediaMetadataCompat;
import android.support.v4.media.session.MediaSessionCompat;
import android.support.v4.media.session.PlaybackStateCompat;

import androidx.core.app.NotificationCompat;
import androidx.media.app.NotificationCompat.MediaStyle;

import com.google.android.exoplayer2.C;
import com.google.android.exoplayer2.MediaItem;
import com.google.android.exoplayer2.PlaybackException;
import com.google.android.exoplayer2.Player;
import com.google.android.exoplayer2.SimpleExoPlayer;
import com.google.android.exoplayer2.audio.AudioAttributes;
import com.google.android.exoplayer2.source.MediaSource;
import com.google.android.exoplayer2.source.ProgressiveMediaSource;
import com.google.android.exoplayer2.upstream.DefaultDataSource;
import com.google.android.exoplayer2.upstream.DefaultHttpDataSource;
import com.google.android.exoplayer2.extractor.DefaultExtractorsFactory;

/**
 * 后台音频播放服务：
 * - ExoPlayer 播放课程音频与电台直播
 * - 前台通知 + 锁屏控制（MediaSession）
 * - 进度/状态回调给 MainActivity 转发给 H5
 */
public class PlaybackService extends Service {

    public static final String CHANNEL_ID = "playback";
    public static final int NOTIF_ID = 1001;

    public interface PlaybackListener {
        void onProgress(long curMs, long durMs);   // 仅课程音频周期回调
        void onStateChanged(boolean playing);
        void onEnded();
    }

    private SimpleExoPlayer player;
    private MediaSessionCompat mediaSession;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private PlaybackListener listener;

    private String kind = "";      // "ep" 课程音频 | "radio" 电台
    private String curTitle = "";
    private String curSub = "";
    private boolean foreground = false;

    public class LocalBinder extends Binder {
        public PlaybackService getService() {
            return PlaybackService.this;
        }
    }

    private final IBinder binder = new LocalBinder();

    @Override
    public void onCreate() {
        super.onCreate();
        player = new SimpleExoPlayer.Builder(this).build();
        AudioAttributes attrs = new AudioAttributes.Builder()
                .setUsage(C.USAGE_MEDIA)
                .setContentType(C.CONTENT_TYPE_SPEECH)
                .build();
        player.setAudioAttributes(attrs, true);
        player.setHandleAudioBecomingNoisy(true);
        // 关键修复：网络唤醒锁 —— 锁屏后 CPU/网络休眠会导致网络流（课程音频/电台直播）中断
        // 需要清单里的 WAKE_LOCK 权限（已声明），ExoPlayer 会自动获取锁并在释放时归还
        player.setWakeMode(C.WAKE_MODE_NETWORK);
        player.addListener(new Player.Listener() {
            @Override
            public void onIsPlayingChanged(boolean isPlaying) {
                if (listener != null) listener.onStateChanged(isPlaying);
                updateNotification();
                updateMediaSession();
                if (isPlaying && "ep".equals(kind)) {
                    scheduleProgress();
                } else {
                    mainHandler.removeCallbacks(progressRunnable);
                }
            }

            @Override
            public void onPlaybackStateChanged(int playbackState) {
                if (playbackState == Player.STATE_ENDED) {
                    if (listener != null) listener.onEnded();
                }
                updateMediaSession();
            }

            @Override
            public void onPlayerError(PlaybackException error) {
                if (listener != null) listener.onEnded();
                stopPlayback();
            }
        });

        mediaSession = new MediaSessionCompat(this, "EnglishBuddyMediaSession");
        mediaSession.setActive(true);
        mediaSession.setCallback(new MediaSessionCompat.Callback() {
            @Override
            public void onPlay() { resume(); }

            @Override
            public void onPause() { pause(); }

            @Override
            public void onStop() { stopPlayback(); }

            @Override
            public void onSeekTo(long pos) {
                if (player != null) player.seekTo(pos);
            }

            @Override
            public void onRewind() { skip15(false); }

            @Override
            public void onFastForward() { skip15(true); }
        });
    }

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (action == null) {
            // 由 startForegroundService 无动作拉起：立即进入前台，
            // 满足 Android 8+ 「5 秒内必须 startForeground」约束，否则应用会崩溃
            ensureForeground();
        } else {
            switch (action) {
                case "toggle":
                    if (isPlaying()) pause(); else resume();
                    break;
                case "rew15": skip15(false); break;
                case "fwd15": skip15(true); break;
                case "stop": stopPlayback(); break;
            }
        }
        return START_NOT_STICKY;
    }

    public void setListener(PlaybackListener l) {
        this.listener = l;
    }

    /* ---------------- 对 JS Bridge 开放的控制接口 ---------------- */

    public void playEpisode(String url, String title, String sub) {
        play(url, title, sub, "ep");
    }

    public void playLive(String url, String title, String sub) {
        play(url, title, sub, "radio");
    }

    private void play(String url, String title, String sub, String k) {
        if (player == null) return;
        kind = k;
        curTitle = title == null ? "" : title;
        curSub = sub == null ? "" : sub;

        DefaultHttpDataSource.Factory httpFactory = new DefaultHttpDataSource.Factory()
                .setUserAgent("EnglishBuddy/1.0 (Android)")
                .setConnectTimeoutMs(15000)
                .setReadTimeoutMs(15000)
                .setAllowCrossProtocolRedirects(true);
        DefaultDataSource.Factory dsFactory = new DefaultDataSource.Factory(this, httpFactory);
        ProgressiveMediaSource.Factory srcFactory = new ProgressiveMediaSource.Factory(
                dsFactory, new DefaultExtractorsFactory());
        // 注意：电台流有的以 .m3u 命名但实际是 audio/mpeg 渐进流，
        // 这里统一按 Progressivce 流处理（嗅探器自动识别 MP3/AAC），不按 HLS 解析
        MediaSource source = srcFactory.createMediaSource(MediaItem.fromUri(url));
        player.setMediaSource(source);
        player.prepare();
        player.setPlayWhenReady(true);
        ensureForeground();
        updateNotification();
        updateMediaSession();
    }

    public void pause() {
        if (player != null) player.setPlayWhenReady(false);
    }

    public void resume() {
        if (player != null && !kind.isEmpty()) player.setPlayWhenReady(true);
    }

    public void stopPlayback() {
        if (player != null) {
            player.stop();
            player.clearMediaItems();
        }
        kind = "";
        mainHandler.removeCallbacks(progressRunnable);
        if (listener != null) listener.onStateChanged(false);
        if (foreground) {
            stopForeground(true);
            foreground = false;
        }
        stopSelf();
    }

    public void skip15(boolean forward) {
        if (player == null) return;
        long pos = player.getCurrentPosition();
        player.seekTo(Math.max(0, pos + (forward ? 15000 : -15000)));
    }

    public void seekPercent(int pct) {
        if (player == null || "radio".equals(kind)) return;
        long dur = player.getDuration();
        if (dur > 0) player.seekTo(dur * pct / 100);
    }

    public boolean isPlaying() {
        return player != null && player.isPlaying();
    }

    /* ---------------- 进度上报 ---------------- */

    private final Runnable progressRunnable = new Runnable() {
        @Override
        public void run() {
            if (player != null && "ep".equals(kind) && listener != null) {
                long dur = player.getDuration();
                listener.onProgress(player.getCurrentPosition(), dur < 0 ? 0 : dur);
            }
            if (player != null && player.isPlaying()) {
                mainHandler.postDelayed(this, 500);
            }
        }
    };

    private void scheduleProgress() {
        mainHandler.removeCallbacks(progressRunnable);
        progressRunnable.run();
    }

    /* ---------------- 通知与前台 ---------------- */

    private void ensureForeground() {
        // 直接进入前台（无条件），满足 startForegroundService 的 5 秒约束；
        // 无内容时展示“就绪”通知，play() 后由 updateNotification 刷为实际内容
        Notification n = buildNotification();
        startForeground(NOTIF_ID, n);
        foreground = true;
    }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel ch = new NotificationChannel(CHANNEL_ID,
                        getString(R.string.channel_name),
                        NotificationManager.IMPORTANCE_LOW);
                nm.createNotificationChannel(ch);
            }
        }
    }

    private NotificationCompat.Builder baseBuilder() {
        ensureChannel();
        boolean playing = isPlaying();
        boolean empty = kind.isEmpty();

        NotificationCompat.Builder nb = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_note)
                .setContentTitle(empty ? getString(R.string.app_name) : curTitle)
                .setContentText(empty ? getString(R.string.notif_ready) : curSub)
                .setContentIntent(contentIntent())
                .setOngoing(playing && !empty)
                .setOnlyAlertOnce(true)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setCategory(NotificationCompat.CATEGORY_TRANSPORT)
                .setShowWhen(false);

        if (!empty) {
            if ("ep".equals(kind)) {
                nb.addAction(R.drawable.ic_rew15, "后退15秒", actionIntent("rew15"));
            }
            nb.addAction(playing ? R.drawable.ic_pause : R.drawable.ic_play,
                    playing ? "暂停" : "播放", actionIntent("toggle"));
            nb.addAction(R.drawable.ic_stop, "停止", actionIntent("stop"));

            int[] compact = "ep".equals(kind) ? new int[]{0, 1, 2} : new int[]{0, 1};
            nb.setStyle(new MediaStyle()
                    .setMediaSession(mediaSession.getSessionToken())
                    .setShowActionsInCompactView(compact));
        }
        return nb;
    }

    private Notification buildNotification() {
        return baseBuilder().build();
    }

    private void updateNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        ensureChannel();

        boolean empty = kind.isEmpty();
        Notification n = buildNotification();
        if (!empty) {
            startForeground(NOTIF_ID, n);
            foreground = true;
        } else if (foreground) {
            nm.notify(NOTIF_ID, n);
            stopForeground(false);
            foreground = false;
        }
    }

    private PendingIntent contentIntent() {
        Intent i = new Intent(this, MainActivity.class);
        i.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getActivity(this, 0, i, flags);
    }

    private PendingIntent actionIntent(String action) {
        Intent i = new Intent(this, PlaybackService.class);
        i.setAction(action);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getService(this, action.hashCode(), i, flags);
    }

    /* ---------------- MediaSession（锁屏/耳机控制） ---------------- */

    private void updateMediaSession() {
        if (mediaSession == null) return;
        boolean playing = isPlaying();
        long pos = player == null ? 0 : player.getCurrentPosition();
        long dur = (player == null || "radio".equals(kind)) ? -1 : player.getDuration();
        if (dur < 0) dur = -1;

        long actions = PlaybackStateCompat.ACTION_PLAY
                | PlaybackStateCompat.ACTION_PAUSE
                | PlaybackStateCompat.ACTION_PLAY_PAUSE
                | PlaybackStateCompat.ACTION_STOP
                | PlaybackStateCompat.ACTION_SEEK_TO
                | PlaybackStateCompat.ACTION_REWIND
                | PlaybackStateCompat.ACTION_FAST_FORWARD;

        PlaybackStateCompat.Builder sb = new PlaybackStateCompat.Builder()
                .setActions(actions)
                .setState(playing ? PlaybackStateCompat.STATE_PLAYING
                                : (kind.isEmpty() ? PlaybackStateCompat.STATE_NONE
                                                  : PlaybackStateCompat.STATE_PAUSED),
                        pos, playing ? 1.0f : 0f);
        mediaSession.setPlaybackState(sb.build());

        MediaMetadataCompat.Builder mb = new MediaMetadataCompat.Builder()
                .putString(MediaMetadataCompat.METADATA_KEY_TITLE,
                        kind.isEmpty() ? getString(R.string.app_name) : curTitle)
                .putString(MediaMetadataCompat.METADATA_KEY_ARTIST,
                        kind.isEmpty() ? "" : curSub)
                .putString(MediaMetadataCompat.METADATA_KEY_GENRE,
                        "radio".equals(kind) ? "LIVE" : "Lesson");
        if (dur > 0) mb.putLong(MediaMetadataCompat.METADATA_KEY_DURATION, dur);
        mediaSession.setMetadata(mb.build());
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // 用户划掉应用：未在播放则随之停止；正在播放则继续（前台服务保活）
        if (player == null || !player.isPlaying()) {
            stopPlayback();
        }
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        mainHandler.removeCallbacks(progressRunnable);
        if (mediaSession != null) {
            mediaSession.setActive(false);
            mediaSession.release();
            mediaSession = null;
        }
        if (player != null) {
            player.release();
            player = null;
        }
    }
}
