# 英语小伙伴（xiaohuoban-english）

VOA Learning English 英语学习 App：H5 主站 + Android WebView 壳 + 内容生产流水线，内容在线地址 <https://www.jxonline.top/app/>。

## 项目结构

```
├── site/        H5 主站（部署到 VPS 的完整站点）
│   ├── index.html      单文件前端：音频课程/视频课程、难度分级、点词查义、生词收藏、单词本
│   ├── api/            manifest.json（课程目录）、dict/（按首字母分块的词典）、stations.json
│   └── articles/       每集课文 JSON（含正文 HTML/MD、摘要、元信息）
├── android/     Android 壳（WebView + JsBridge + MediaSession 播放服务）
│   └── app/src/main/java/com/andyluoqin/englishbuddy/
│       ├── MainActivity.java   WebView 容器、返回键路由、APK 安装更新
│       ├── JsBridge.java       JS↔Native 桥（版本检测、安装 APK）
│       └── PlaybackService.java 后台播放服务（通知栏控制、MediaSession）
├── pipelines/   内容生产流水线（Python，从 VOA 抓取→音频压缩→上传 VPS→更新 manifest）
└── docs/        内容规划文档
```

## 内容生产流水线（pipelines/）

| 脚本 | 作用 |
|---|---|
| orchestrator.py | 抓取编排主循环，串起全部节目 |
| fetch_programs.py / fetch_video_programs.py | 抓取节目清单（音频/视频） |
| fetch_articles.py | 抓单节目文章正文、元信息、音频直链 |
| audio_downloader.py | 批量补齐缺失音频（curl 并行，CDN 优先代理兜底） |
| pipeline_upload.py | 音频流水线：ffmpeg 压缩(48k单声道) → 文章 JSON 生成 → SFTP 上传 → manifest 原子更新（内置 Lesson 数字排序、多进程文件锁） |
| video_cdn_pipeline.py | 视频流水线：解析多档清晰度 → CDN 直链入 manifest（VPS 不存视频） |
| watchdog.py | 流程看护 |

> 访问 VPS 的脚本通过环境变量 `VPS_PWD` 读取密码，运行前先设置。

## Android 构建

1. JDK 1.8、Gradle 7.5.1、Android SDK 30
2. 在 `android/` 下创建 `keystore.properties`（参考 `keystore.properties.example`），并把 keystore 文件放到 `android/app/`
3. 构建：`gradle assembleRelease`（签名已自动配置）

## 部署（VPS）

站点部署于 `www.jxonline.top`，Caddy 反代静态目录 `/var/www/xiaohuoban/app`；音频/视频走 VOA CDN 直链，VPS 只存文章 JSON 与 manifest。

> AI生成
