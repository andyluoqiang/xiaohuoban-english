# 英语小伙伴「AI 陪练」实时语音对话功能方案

> 基于 2026-10-19 实测（MiniMax 订阅 Key + 各厂商官网调研 + 阿里 Realtime P0 实测）整理
> 状态：方案稿 v3.1（P2.6 完成：悬浮按钮可拖动）

---

## 一、背景与需求

英语小伙伴 App 新增全局功能「AI 陪练」：点击图标进入**实时 AI 英语对话**，像电话聊天一样，可随时打断。

用户拍板要点（2026-10-14）：①一步到位做全双工实时打断，不做逐句过渡；②key 先放 App 里；③先形成方案，暂不改代码。

## 二、MiniMax 能力实测结论（2026-10-19，真实调用验证）

| 能力 | 接口 | 实测结果 | 说明 |
|---|---|---|---|
| 文本对话 | `POST /v1/chat/completions`（OpenAI 兼容） | ✅ 200 | MiniMax-M2.7 / M3.1-Flash 均可调用，支持流式 |
| 语音合成 TTS | `POST /v1/t2a_v2` | ✅ 200 | 返回 base64 MP3；speech-2.5-hd-preview / speech-02-hd 可用；音色 `female-shaonv` 有效 |
| 流式 TTS（WebSocket） | `/ws/v1/t2a_v2` | ✅ 文档确认 | 按句发送文本，流式返回音频，低延迟播报 |
| **双向流式 TTS（可打断）** | `/ws/v1/t2a_v2_bidi` | ✅ 文档确认 | 逐字流式输入、服务端攒句、**task_cancel 打断**、task_flush 催句 |
| 语音识别 ASR | `POST /v1/speech_to_text` | ✅ 200 | 模型 asr-1.0；SSE 流式返回、说话人分离、字幕；音频≤500s/50MB |
| 模型原生音频输出 | chat 的 `audio_content` 字段 | ⚠️ 字段存在但为空 | M3.1-Flash 声明支持，实测未返回音频数据，不作为依赖 |
| **全双工 Realtime API** | 多路径 WebSocket 探测 | ❌ 全部 404 | **MiniMax 无现成的「全双工实时语音对话」一体 API**，只能 ASR+LLM+TTS 三段拼接 |

## 三、实时语音服务选型对比（2026-10 调研，官网现行价）

| 对比项 | **阿里云百炼**（首选） | 火山引擎·豆包 | MiniMax | 硅基流动 |
|---|---|---|---|---|
| 实时一体 API | ✅ **Qwen-Omni-Realtime** | ✅ Doubao-Seed-RealtimeVoice | ❌ 无 | ❌ 无 |
| 形态 | 端到端全双工（WS/WebRTC/AOQ） | 端到端全双工（火山方舟） | 仅 ASR/TTS/LLM 分离 | 仅 ASR/TTS 分离 |
| 音频输入价 | **6 元/百万 token** | 80 元/百万 token | 按时长/字符另计 | TTS 0.05元/千字符 |
| 音频输出价 | **12 元/百万 token** | 80 元/百万 token | 按时长/字符另计 | — |
| 文本输入/输出价 | 1.5 / 4.5 元/百万 token | 统一 80/80 | 订阅内 | 按模型 |
| 免费额度 | **100 万 token（北京，90天）** | 新客券/资源包 | 订阅 key | 部分免费 |
| 打断能力 | ✅ 服务端 VAD / semantic_vad（过滤附和声、防误触发） | ✅ 更自然打断（时延约1秒/裸模型700ms） | ⚠️ 需自拼 task_cancel | ❌ |
| 语种 | 识别 113 种 / 合成 36 种 | 主流方言+普通话见长 | 多语种 | 取决于模型 |
| 音色复刻 | ✅ 声音复刻 | ✅ 5 秒复刻 | ✅ | ❌ |
| 接入生态 | 官方 Python/Java SDK、三种协议 | 火山方舟 SDK | OpenAI 兼容 | OpenAI 兼容 |
| 官网 | https://help.aliyun.com/zh/model-studio/realtime | https://www.volcengine.com/product/realtime-voice-model | https://platform.minimax.cn/docs | https://www.siliconflow.cn/models |
| 备注 | **性价比最高**；北京地域需业务空间 ID | 中文情感/方言演绎最强，价格约阿里 7~13 倍 | 已有订阅 key 可做降级备选 | 无实时对话能力，排除 |

**选型结论**：英语陪练场景**首选阿里 qwen3.8-omni-flash-realtime**——单价低一个数量级（音频输入便宜约 13 倍、输出约 6.7 倍）、有免费额度、支持声音复刻（可定制亲和的美式发音老师）、官方 SDK 齐全；**豆包作为备选**（中文情感/方言表现突出时切换）；**MiniMax 拼接方案降级为兜底路线**（现有 key 零成本可验证，但只能做到"对讲机式"交互）。

⚠️ 注意：各家音频 token 换算口径可能不同（每秒语音对应 token 数定义不一样），最终"每分钟对话成本"建议各自充值后用真实录音各实测一轮。

## 四、架构设计

### 主路线：阿里 Qwen-Omni-Realtime（一站式全双工）

```
H5/App 录音(16kHz PCM)
   ↕ WebSocket（wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime）
Qwen3.8-Omni-Flash-Realtime 服务端
   ├─ 输入：流式音频（1/2/4 声道）、文本、图像帧
   ├─ 输出：流式音频（24kHz PCM）+ 文本字幕
   ├─ 轮次检测：server_vad / semantic_vad（自动判断说话起止，天然支持打断）
   └─ 指令：session.update 配置音色/instructions（英语老师人设）
播放（24kHz PCM 边收边播）+ 字幕上屏
```

一次会话一个 WebSocket 连接，无需客户端编排 ASR/LLM/TTS 三段流水线；打断由服务端 VAD 自动处理（用户开口即停播并切换聆听）。

### 备选路线 A：火山豆包实时语音（情感/方言增强时切换）
形态与主路线相同（端到端全双工），接入走火山方舟；单价高，仅在需要更强中文情感演绎时启用。

### 备选路线 B：MiniMax 三段拼接（现有 key 兜底，"对讲机式"）

```
录音 → ASR(/v1/speech_to_text) → LLM(/v1/chat/completions, M3.1-Flash)
    → TTS(/ws/v1/t2a_v2_bidi 逐token攒句) → 播放 ← 打断: task_cancel
```

单轮延迟约 2~4 秒，为"对讲机式"交互；适用于主路线故障/额度耗尽时降级。

### 适配层设计

三路线统一抽象为 `RealtimeSession` 接口（connect / updateSession / appendAudio / onAudioDelta / onTextDelta / onInterrupted / close），主备切换只改实现类，UI 层无感。

## 五、涉及工作清单

### A. 接入层（主路线：阿里 Realtime）
1. **会话管理**：业务空间 ID + API Key 配置、WebSocket 建连/重连（120s 空闲保活）、session.update（modalities=text+audio、音色、instructions 英语老师人设、turn_detection 模式选择）
2. **音频采集/播放**：16kHz 16bit 单声道 PCM 采集分块（3200 字节/块）、24kHz PCM 流式播放队列
3. **事件处理**：response.audio.delta / response.text.delta / input_audio_transcription（实时字幕）/ speech_started（打断）/ error 重试

### B. 入口与 UI（H5 层）
4. **全局入口**：底部导航/首页加「AI 陪练」图标
5. **对话页**：录音状态指示（聆听/思考/说话三态）、对话气泡（用户/AI）、实时字幕（输入转写+输出字幕）、挂断按钮
6. **打断交互**：服务端 VAD 自动打断为主 + 手动点麦克风强制打断兜底
7. **设置项**：音色选择、语速、AI 人设（口语老师/语伴）、难度分级

### C. 鉴权与安全（key 先放 App 里，用户已拍板）
8. Key 存 App 本地存储；web 版提示暴露风险；正式分发建议后端中转
9. 会话超时、错误友好提示（配额用尽/断网/限流 60RPM）

### D. 稳定性与体验
10. 弱网重连（WebRTC/AOQ 协议备选，内置回声消除与降噪）
11. Android 6.0+ 录音动态权限；后台/锁屏暂停
12. 用量监控（音频 token 消耗统计，防止费用失控）

## 六、分阶段计划（建议）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0（先行验证，✅ 已完成 2026-10-19）** | 开通阿里百炼 key（北京地域 + 业务空间 ID）→ Python 脚本实测一轮"录音→AI 语音回复"（验证时延/音色/打断） | ✅ 命令行完成一轮真实对话，实测数据见下节 |
| P1 | H5 原型页（麦克风实时对话 + 字幕 + 打断），部署到网页版验证 | **✅ 已完成 2026-10-19**，线上地址：https://www.jxonline.top/app/lab/realtime.html |
| P2 | 完整对话页 UI + 设置项 + 多轮上下文 + 状态机 | **✅ 主体完成 2026-10-19**：全局悬浮按钮 + 全屏对话浮层已集成进 App（首页/广播/生词本/设置任意页可见），线上即时生效（index.html no-store）；设置项/多轮上下文细化待 P3 |
| P3（可选） | 豆包备选接入（适配层）；后端密钥中转；音色复刻定制 | 安全加固 + 双供应商容灾 |

### P0 实测记录（2026-10-19，Windows + Python websocket-client，Manual 模式）

- 模型：`qwen3.8-omni-flash-realtime`（华北2北京，业务空间绑定 WebSocket 直连）
- 输入：7.6 秒英文语音（16kHz/16bit/mono PCM），instructions 设为美式英语老师人设，音色 Tina
- **实测延迟**：session.created ≈1ms；音频上传+commit 374ms；**首字文本 +740ms；首音 +1007ms**；整轮 2.0s
- **回复**：自然口语文本 + 7.0 秒 24kHz PCM 语音（337,920 字节）
- **成本**：输入 788 / 输出 112 token ≈ **0.006 元/轮**；连续对话估算约 0.02~0.05 元/分钟；同轮按豆包单价估算约 0.07 元（**实测口径阿里便宜约 12 倍**）
- 结论：**全链路验证通过，1 秒级首音延迟达到"电话式"体验标准，成本可忽略**；服务端 VAD 自动打断留待 P1 H5 原型验证

### P1 实施记录（2026-10-19，WebRTC 版原型已上线）

- **关键架构决策**：浏览器原生 WebSocket 无法携带 Authorization 鉴权头（W3C 规范限制），因此浏览器端不走 WebSocket 直连，改用官方 WebRTC 接入：`RTCPeerConnection`（无 ICE 服务器，服务端处理 NAT）+ `oai-events` DataChannel 交换事件 + 音频 RTP 直传（内置回声消除/降噪），语义 VAD（semantic_vad）自动断句/插话打断。
- **SDP 交换鉴权**：阿里端点有 CORS 限制不能浏览器直连，由服务端代理——线上用 VPS Caddy 反代（`handle /api/v1/webrtc/realtime` → 上游 maas.aliyuncs.com，`header_up Authorization` 注入密钥）。**密钥仅存于服务器，页面零密钥**（优于初期"key 放 App"方案，属官方推荐的 AppServer 模式）。
- **实现要点**：媒体门控（addTrack 后 enabled=false + replaceTrack(null)，session.created 后恢复）；Answer SDP 逐行 trim、`\r\n` 规范化；用户语音实时转写（qwen3-asr-flash-realtime）与 AI 回复流式字幕同屏；演示模式用 MediaStreamDestination 注入测试语音（无麦克风环境可验证）。
- **验证结果**（本地 127.0.0.1 与线上 www.jxonline.top 双环境）：WebRTC 连通、会话就绪、演示语音触发 VAD 分段转写、**AI 真实语音回复两轮**（"That sounds great! I'd love to chat with you..."），插话打断链路生效。Caddy 修改均经 validate + 备份 + 回滚机制。
- **代码位置**：`site/lab/realtime.html`（单文件，含演示模式）+ `test_input.wav`；git 已提交推送（9039750 / a0af12c）。VPS 新增路由见 `/etc/caddy/sites/www.jxonline.top.conf`（含 4 份 .bak 备份）。

### P2 实施记录（2026-10-19，悬浮按钮 + 对话浮层已上线 App）

- **入口（用户交互拍板）**：右下角**悬浮图片按钮**（卡通猫头鹰 mascot 戴耳机举麦克风，Azure 渐变，AIGC 生成并 360px 压缩，水印按规范保留）+「AI陪练」胶囊标签；位于 `#app` 之外的固定层（不受 render() 页面切换影响，**任意页面可见**）；首访显示引导气泡「随时开口练英语·点我开始对话」，localStorage 记忆仅首次。
- **对话浮层**：全屏上滑入，藏蓝渐变头部（连接点+关闭），聊天区仅用户/AI 气泡；**连接过程信息零展示**（用户要求），仅状态机三态（聆听中/思考中/老师说话中）与音量条；报错才出现 toast + 红色错误气泡。
- **健壮性**：ICE 闪断（disconnected）宽容处理——观察 8 秒等待自动恢复不误报，仅 failed/closed 判定断开；打开浮层自动停止课文/电台播放；Android 硬件返回键优先关闭浮层；关闭即全量资源清理（轨道/PeerConnection/AudioContext）。
- **验证结果**（本地+线上双环境）：悬浮按钮四页可见；首访气泡、返回用户隐藏；点击连接约 3s 就绪；注入演示语音两轮对话（VAD 分段转写 + AI 语音回复）无报错无过程信息；打断按钮、关闭清理、nativeBack 全部通过。线上地址：https://www.jxonline.top/app/
- **代码**：`index.html`（AI 模块，含 aiOpen/aiSession/aiHandle 状态机）+ `img/ai_fab.png`；git 提交 abcfc64。
- **遗留（P3）**：音色/难度设置项、多轮上下文长度控制、响应过长的打断策略调优；豆包备选接入；后端密钥中转优化（当前 Caddy 承担）。

### P2.1 麦克风授权修复（2026-10-19 晚，H5+原生双端）

- **问题**：App 内点开 AI 陪练报"麦克风不可用"，用户找不到授权入口。根因：① 安卓壳 Manifest 缺 `RECORD_AUDIO`；② WebView `onPermissionRequest` 未实现——getUserMedia 的网页权限请求被静默拒绝，系统授权弹窗根本不出现。
- **H5 修复（用户交互拍板）**：首次点开 AI 陪练弹**授权提示框**（"开启语音对话"卡片：麦克风采音说明 + 「授权并开始」/「暂不使用」），同意后才调起系统/浏览器授权；仅首次弹（localStorage `aiMicIntro`），拒绝则关浮层+toast 提示可随时重开。麦克风错误细分引导：NotAllowed → **App 版**"手机设置→应用管理→英语小伙伴→权限" / **浏览器版**"地址栏权限图标"；未插设备、被占用、其他错误各配对应文案；**错误态下「打断」按钮变「↻ 重试」**，一键清理残留重连（会话就绪自动恢复为打断按钮）。
- **原生修复（englishbuddy v1.0.5 / versionCode 5）**：Manifest 新增 RECORD_AUDIO + MODIFY_AUDIO_SETTINGS；MainActivity 实现 `onPermissionRequest`——WebView 权限请求转为系统运行时权限弹窗（ActivityCompat），授权回调 grant/deny 给 WebView，拒绝时 Toast 指引到系统设置；`onPermissionRequestCanceled` 兜底。
- **验证**：本地+线上（授权框首弹/再开直连/拒绝记忆/模拟 NotAllowedError 引导文案/重试恢复）全过；APK 构建成功（`android/app/build/outputs/apk/release/englishbuddy-1.0.5.apk`，约 3MB）。**App 需卸载旧版或直接安装覆盖 1.0.5 生效**（升级后 WebView 权限链路才可用）；网页版已即时生效。
- git：b80dfbb。

### P2.2 自动打断 + 音色选择（2026-10-19 晚）

- **自动打断（用户拍板：不用按钮，检测到用户开口自动停止）**：
  - 移除「✋ 打断」按钮；打断完全由服务端 semantic VAD 驱动（`input_audio_buffer.speech_started`）。
  - **关键修复**：事件抓包发现 WebRTC 模式下服务端**不下发** `response.output_audio_started`（AI 音频走 RTP，字幕走 `response.audio_transcript.delta`），原以该事件置位的 `AI.speaking` 从未生效（即打断判断从未激活）。改为在 `response.created` 置位 speaking，首个字幕 delta 二次防御。
  - 打断动作：`response.cancel` + **立即禁用远端音频轨**（消除已缓冲尾巴，立即静音）+ AI 气泡标注「✂（已打断）」+ 状态复位聆听；新回复 `response.created` 自动解除静音。
  - 错误态保留「↻ 重试」按钮（仅出错时显示）。
- **音色选择（官方支持，`session.audio.output.voice` + 顶层 `voice` 双写）**：
  - 对话浮层头部新增音色按钮（显示当前音色名）；底部弹层 10 款音色：**女 5**（Tina 甜甜/Jennifer 詹妮弗·美式/Mione 敏儿·英式/Serena 苏瑶/Mia 舒然）+ **男 5**（Aiden 艾登·美式/Raymond 林川野/Evan 江晨/Theo Calm 予安/Ryan 甜茶），均为 qwen3.8-omni 系列（28 种外语含英文），附中文名/英文名/风格描述。
  - 会话中切换：立即 `session.update` 新音色 → 老师用新声音打招呼（预览，真实走 RTP 音频）；未连接时持久化 localStorage 下次会话生效。
- **验证**（本地+线上）：自动打断全链路（cancel 发送/远端静音/气泡标注/新回复解除静音）、会话中切 Jennifer 与 Aiden 均返回新音色预览问候、错误重试恢复、音色持久化——全部通过，零 error 事件。
- 附注：免提外放场景存在回声误触发打断的理论风险（测试环境复现过回声 speech_started）；真机依靠浏览器/WebRTC AEC + semantic_vad 抑制，建议真机验证，戴耳机无此问题。
- git：0d46df5。

### P2.3 移除对话区状态提示条（2026-10-19 晚，用户反馈）

- **问题**：对话区下方的白色提示条（"已打断，你说… / 正在听你说…"）遮挡聊天文字（用户截图反馈：英文气泡仅显示上半行）。
- **处理**：整体删除 ailive 提示条（HTML/CSS/aiLive 函数及全部调用点，grep 清零）；状态反馈统一到底部状态行——用户停止说话后短暂显示「🧠 思考中…」，其余沿用「聆听中/老师说话中」；聊天区可视高度相应增加。
- **验证**：本地+线上回归——完整对话、自动打断、状态切换正常，布局无重叠（getBoundingClientRect 校验 + 截图确认）；git：f5b250f。

### P2.4 老师名字跟随音色联动（2026-10-19 晚）

- **问题**：原人设指令只定义身份未指定名字，AI 被问名字时自称 "Alex"（模型即兴编造），与所选音色无关，换音色后名字也不变。
- **方案**：`AI.INST` 改为 `AI.INST_TPL` 模板（`You are {NAME}, ... {NAME} is the name you go by — always use it when asked your name, never invent another.`）；`aiInst()` 按当前音色实时替换 `{NAME}` 注入 instructions（`aiVoiceCur()` → `aiFindVoice()` 定位音色，无效值兜底 Tina）；`aiSessionCfg()` 改用 `aiInst()`；切音色问候语改为 "Greet me in your new voice and introduce your name in one short sentence."，换音色即随新名字自我介绍。
- **验证**：本地端到端——选 Aiden 音色发文本 "what is your name"，AI 回复 "Hi there! I'm Aiden, your friendly American English teacher."；线上 evaluate 确认模板/函数/问候语部署在位。测试中 voiceHeader 曾显示旧音色名，属测试脚本直改 localStorage 的假象（按钮文本仅页面加载时同步），真实用户流程无此问题，未做修复。
- **范围**：仅改 `index.html`（13 增 3 删）；部署 VPS（index.html 110,181 字节 + ai_fab.png）；git：69ecc1a（一并补推上次滞留的 b239b99）。
- **会话历史**：用户拍板暂不做（成本/隐私/复杂度分析已留档此前讨论）。

### AOQ 协议 Token 鉴权研究（2026-10-19 晚，官方文档 + 实测）

- **机制**：服务端代理鉴权（AppServer 模式）。AppServer 携 API Key 调 `POST /api/v1/webrtc/realtime?model=xxx`（Header `x-dashscope-rtc-transport: moq`，body 可传 `clientIp` 就近分配），网关返回**一次性连接包**：`sid`（会话ID）、`aoqTokenForClient`（32位客户端令牌）、`clientRelayEndpoints`（Relay中继 IP:8443）、`clientRelayCertFingerprint`（证书指纹）、`sidExpiresInSecs`（默认 7200 秒）。客户端将 token/sid/relayEndpoints/certFingerprint 填入 AOQ SDK `engine.connect()` 直连 Relay；建连后传输不再鉴权；**API Key 全程不出服务端**，泄露的 Token 也只有 2 小时单会话生命周期。
- **实测**（2026-10-19，用户 key）：HTTP 200 一步成功，返回结构完整（2 个北京 Relay 节点 47.95.245.226 / 47.100.207.43:8443）。
- **SDK 平台（v1.3.0）**：Android(AAR+Opus插件)/iOS/鸿蒙/Windows/macOS/Linux/`aoq-electron-sdk`(npm)；**无浏览器/WebView H5 SDK**（MoQ 浏览器不支持）。
- **对本项目结论**：
  1. 英语小伙伴为 WebView H5 → **AOQ 当前用不上**，维持 WebRTC+Caddy 反代（"key 不出服务端"已与 AOQ 等价）；
  2. 与 AOQ 的真实差距在**用户级管控**：现在 Caddy 裸代理"知 URL 即可用"；AOQ 模式每次建连经自家 AppServer 下发 Token → 可前置登录/限流/额度。公开前可在 Caddy 前补一层自有鉴权等价实现，改动小；
  3. 长期原生 App 化时（播放器/发音评测下沉），可引入 Android AAR 迁移 AOQ，获得标准鉴权 + QUIC/Relay 弱网优势；
  4. **小伙伴办公（Electron）可直接 `npm install aoq-electron-sdk`**——未来办公助手加实时语音的开箱即用路线。

### 接口访问码门禁（2026-10-19 深夜，安全加固，已上线）

- **目标**：`https://www.jxonline.top/api/v1/webrtc/realtime` 原为 Caddy 裸代理（“知 URL 即可用”，存在被盗刷风险），改造为仅口令持有者可用。
- **方案（单人口令模型）**：Caddy 在该 handle 块加 `basic_auth`（bcrypt 哈希存储，用户名 luo + 16 位强随机访问码）→ 无/错凭证请求直接 401，不转发到阿里（零费用风险）；鉴权通过后 Caddy 仍覆写 Authorization 为真实阿里 key。密钥继续仅存服务器，前端零密钥。
- **前端**：① index.html：aiGate() 门禁流程（先访问码→再麦克风首用→建会话）；首用弹白色「🔐 输入访问码」卡片（存 xb_aiCode），SDP 请求带 `Authorization: Basic`，遇 401 自动清码重弹应用内输入框。② lab 原型页：startSession 前同源弹码（共用 xb_aiCode 存储）。
- **验证**（VPS curl + Playwright 双侧）：主站/lab 页 200；无凭证 401；错码 401；对码+垃圾 SDP 400（过门禁、达阿里被拒，符合预期）；带显式凭证的 401 不触发浏览器原生弹窗（JS 正常接管）。Caddy 走备份→validate→restart→验证紀律，失败自动回滚。备份：/etc/caddy/sites/www.jxonline.top.conf.bak-codegate-20261019-233542。
- **已知边界**：①旧版页面（部署前加载的）调用接口会弹浏览器原生 Sign in 框（无凭证 401 导致）——取消+刷新即恢复；②访问码为单人口令，未来面向多用户发行时需升级账号体系（每人一码/限流/额度）；③换码 = 重跑脚本改 Caddy 一行 + restart，客户端输新码即可。
- git：3475e16。

### P2.5 聊天区自动滚动修复（2026-10-20 凌晨，用户截图反馈）

- **问题**：对话内容超出窗口高度后，最新内容被底部区域遮挡显示不完整（用户截图："I'm Qwen-Omni. It's nice to meet you!" 后半段被裁）。
- **根因**：`aiBubble()` 仅在创建气泡时滚动一次；但流式字幕（用户转写 delta / AI 字幕 delta）持续追加文本使气泡长高，此后无任何滚动调用，新长出的文字落在可视区下方。P2.3 删除提示条后问题暴露更明显。
- **修复**：新增 `aiScroll(force)` 统一跟滚函数——距底 <90px（正常跟滚场景）或 force 时 rAF 滚底；用户上翻回看时不强制拉底（标准 IM 行为）。接入点：aiBubble 创建 / 用户转写 delta·completed / AI 字幕 delta·done / cancelled 打断标注；聊天区底部 padding 6px→12px，末条与底栏间距更舒适。
- **验证**（本地 8793 + 线上双环境）：注入 8 条历史 + 15 片流式 delta 后「滚动到底」「最后一条完整可见」均通过；上翻 scrollTop=0 后新 delta 不拉底；aiScroll(true) 回底后续滚；测试痕迹清理完毕。
- git：5ef9a0a。

### P2.6 悬浮按钮可拖动（2026-10-23，用户反馈遮挡内容）

- **问题**：悬浮图标固定右下 78px 处，部分页面内容被遮挡看不了。
- **方案**：沿右侧边缘上下拖动（水平锁定只改纵向），拖动与点击自动区分——位移<8px 视为轻点照常打开对话，≥8px 进入拖动改位置；拖动结束位置存 localStorage（`xb_aiFabY`）下次自动恢复；越界自动钙在屏内（上下留 8px 安全边，resize 重算）；拖动后浏览器随触发的 click 被拦截不误开对话；pointer capture 保证划出按钮仍持续跟踪，touch-action:none 防移动端拖动时滚页。
- **实现**：transform: translateY 方案（不动原有 bottom 定位，避开 wrap 容器定位换算）；aiFabClick() 统一点击入口；拖动时隐藏引导气泡、光标变 grab/grabbing。
- **验证**（本地 8793 + 线上双环境，真实鼠标事件）：拖动量精确（-220/-80/-160 逐个标定）、位置记忆与刷新恢复、轻点打开面板、拖后 click 拦截、小位移不误拖、下拖 600px 钙到 70px 屏内。线上已部署、测试痕迹已清。
- git：7512b57。

## 七、风险与注意事项

- **计费口径**：各家音频 token 换算不同，上线前用真实录音实测每分钟成本（阿里有 100 万 token 免费额度可先测）。
- **Key 安全**：✅ 已解决双层防护——阿里真 key 仅存 VPS Caddy 配置；接口层已加访问码门禁（basic_auth），未授权请求 401 拒之门外，不产生费用。
- **地域要求**：阿里 Realtime 需北京/新加坡地域 API Key + 业务空间 ID，WebSocket 地址须绑定业务空间。
- **限流**：阿里 60 RPM / 200 万 TPM，个人陪练场景足够。
- **豆包单价高**：约阿里 7~13 倍，仅按需启用；火山方舟接入需 endpoint 配置。
- **MiniMax 兜底路线**：M3.1-Flash 原生音频输出实测为空，必须走 TTS 通道；仅能"对讲机式"交互。
- **音色**：阿里默认音色 Tina，可复刻定制；英语陪练建议先试官方英文音色列表。

---

*文档：AI陪练方案.md v3.1 · 2026-10-23*

> AI生成