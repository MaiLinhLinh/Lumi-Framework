import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import { liveClient } from "./services/live";
import { SurfaceHost } from "./surface/SurfaceHost";
import { surfaceStore } from "./surface/store";
import { PresentationHost } from "./presentation/PresentationHost";

export function App() {
  const state = useSyncExternalStore(liveClient.subscribe, liveClient.getSnapshot);
  const surface = useSyncExternalStore(surfaceStore.subscribe, surfaceStore.getSnapshot);
  const hasDashboard = state.planning || Boolean(surface.document);
  const [query, setQuery] = useState("");
  const messages = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => { liveClient.start(); }, []);
  useEffect(() => {
    if (messages.current) messages.current.scrollTop = messages.current.scrollHeight;
  }, [state.messages]);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (await liveClient.submitText(query)) setQuery("");
  }
  async function suggest(text: string) {
    setQuery(text);
    if (await liveClient.submitText(text)) setQuery("");
  }
  return <>
    <div className="ambient ambient-one"></div>
    <div className="ambient ambient-two"></div>
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="/" aria-label="Trang chủ Trợ lí ảo chatbot">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M12 3a6 6 0 0 0-6 6v1.2A3.8 3.8 0 0 0 3 14v2a3 3 0 0 0 3 3h1.5v-7H6V9a6 6 0 0 1 12 0v3h-1.5v7H18a3 3 0 0 0 3-3v-2a3.8 3.8 0 0 0-3-3.8V9a6 6 0 0 0-6-6Z"/><path d="M9 20h6v1H9z"/></svg>
          </span>
          <span><strong>Trợ lí ảo chatbot</strong><small>Thời tiết, tin tức và tri thức trong một cuộc hội thoại</small></span>
        </a>
        <button id="clearButton" className="ghost-button" type="button" title="Dọn cuộc hội thoại">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-1 12H8L7 9Zm3 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z"/></svg>
          <span>Dọn cuộc hội thoại</span>
        </button>
      </header>
      <main id="workspace" className={`workspace ${hasDashboard ? "has-dashboard" : "no-dashboard"}`}>
        <section id="welcome" className="welcome" aria-labelledby="welcomeTitle" hidden={hasDashboard}>
          <span className="welcome-kicker">Lumi assistant</span>
          <h1 id="welcomeTitle">Xin chào, hôm nay tôi có thể giúp gì cho bạn?</h1>
          <p>Hãy hỏi về thời tiết, tin tức hoặc một chủ đề bạn quan tâm.</p>
        </section>
        <section id="contentPanel" className="dashboard-panel surface" aria-label="Nội dung trực quan" hidden={!hasDashboard}>
          <div className="panel-heading">
            <div><span id="contentEyebrow" className="eyebrow">Kết quả trực quan</span><h2 id="contentTitle">{hasDashboard ? "Nội dung trực quan" : "Nội dung"}</h2></div>
            <div className="presentation-controls">
              <div id="presentationAvatar" className="presentation-avatar" data-avatar-state={state.avatar} aria-label="Lumi đang chờ">
                <svg viewBox="0 0 96 96" role="img" aria-hidden="true">
                  <defs><linearGradient id="lumiAvatarFill" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#38bdf8"/><stop offset="1" stopColor="#0369a1"/></linearGradient></defs>
                  <path className="avatar-arm avatar-arm-left" d="M25 65c-10 5-13 13-10 20"/>
                  <path className="avatar-arm avatar-arm-right" d="M71 65c10 5 13 13 10 20"/>
                  <path className="avatar-body" d="M25 88c2-17 12-27 23-27s21 10 23 27"/>
                  <circle className="avatar-face" cx="48" cy="39" r="27"/>
                  <path className="avatar-hair" d="M25 35c2-18 14-27 29-23 10 2 17 10 18 23-10-8-29-10-47 0Z"/>
                  <path className="avatar-brow avatar-brow-left" d="M35 36l8-2"/>
                  <path className="avatar-brow avatar-brow-right" d="M53 34l8 2"/>
                  <circle className="avatar-eye" cx="39" cy="43" r="2.5"/><circle className="avatar-eye" cx="57" cy="43" r="2.5"/>
                  <path className="avatar-mouth" d="M40 52c5 4 11 4 16 0"/>
                </svg>
              </div>
              <span id="contentBadge" className="live-badge"><i></i><span>Dữ liệu</span></span>
            </div>
          </div>
          <div className="content-stage">
            <div ref={viewport} id="weatherView" className="weather-view" hidden={!hasDashboard}>
              <SurfaceHost />
              <PresentationHost viewport={viewport}/>
              {state.planning && <div className="lumi-planning-surface" role="status" aria-live="polite">
                <div className="lumi-planning-spinner" aria-hidden="true"></div>
                <span className="lumi-planning-label">Đang chuẩn bị nội dung trực quan</span>
              </div>}
            </div>
            <div id="musicView" className="music-view" hidden>
              <article className="music-player-card">
                <div className="music-video-shell">
                  <iframe id="musicFrame" title="YouTube music player"
                    allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; web-share"
                    referrerPolicy="strict-origin-when-cross-origin" allowFullScreen
                    sandbox="allow-scripts allow-same-origin allow-presentation"></iframe>
                  <div id="musicStopped" className="music-stopped" hidden>
                    <span aria-hidden="true">♪</span><strong>Đã dừng phát nhạc</strong><small>Hãy yêu cầu phát lại khi bạn muốn tiếp tục.</small>
                  </div>
                </div>
                <div className="music-meta"><div className="music-art" aria-hidden="true">♫</div>
                  <div><span id="musicVersion" className="music-version">YouTube</span><h3 id="musicTitle"></h3><p id="musicArtist"></p></div>
                </div>
              </article>
            </div>
          </div>
        </section>
        <aside id="chatPanel" className="chat-panel surface" aria-label="Hội thoại">
          <div className="chat-heading">
            <div><span className="eyebrow">Trợ lí ảo</span><h2>Hội thoại</h2></div>
            <span id="connectionStatus" className="connection-status">{state.connection}</span>
          </div>
          <div id="messages" className="messages" aria-live="polite" ref={messages}>
            {state.messages.map(message => <article key={message.id} className={`message ${message.role === "user" ? "user" : "debug-trace"}`}>
              {message.role === "user" && <span className="avatar" aria-hidden="true">B</span>}
              <div className="bubble" data-final={message.final === undefined ? undefined : String(message.final)}>{message.text}</div>
            </article>)}
          </div>
          <output id="firstTextLog" className="first-text-log" aria-live="polite" hidden></output>
          <div id="suggestions" className="suggestions" aria-label="Câu hỏi gợi ý">
            <button type="button" data-query="Thời tiết Hà Nội hôm nay thế nào?" onClick={() => void suggest("Thời tiết Hà Nội hôm nay thế nào?")}>Thời tiết Hà Nội hôm nay</button>
            <button type="button" data-query="Tin công nghệ mới nhất là gì?" onClick={() => void suggest("Tin công nghệ mới nhất là gì?")}>Tin công nghệ mới nhất</button>
            <button type="button" data-query="Xin chào" onClick={() => void suggest("Xin chào")}>Xin chào</button>
          </div>
          <form id="chatForm" className="composer" onSubmit={event => void send(event)}>
            <label className="sr-only" htmlFor="queryInput">Nhập câu hỏi của bạn</label>
            <textarea id="queryInput" rows={1} maxLength={8000} placeholder="Nhập câu hỏi của bạn" required value={query} onChange={event => setQuery(event.target.value)}></textarea>
            <button id="microphoneButton" className={`microphone-button${state.listening ? " listening" : ""}`} type="button" aria-label="Bắt đầu nói" aria-pressed={state.listening} title="Nói với Lumi"
              onClick={() => liveClient.toggleMicrophone()} onKeyDown={event => {
                if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return;
                event.preventDefault(); liveClient.toggleMicrophone();
              }}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a1 1 0 1 0-2 0 3 3 0 0 1-6 0 1 1 0 1 0-2 0 5 5 0 0 0 4 4.9V20H8a1 1 0 1 0 0 2h8a1 1 0 1 0 0-2h-3v-3.1A5 5 0 0 0 17 11Z"/></svg>
            </button>
            <button id="sendButton" type="submit" aria-label="Gửi câu hỏi"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 17 8-17 8 3-7 8-1-8-1-3-7Z"/></svg></button>
          </form>
          <p id="voiceStatus" className="voice-status" role="status" aria-live="polite">{state.voice}</p>
          <p className="composer-hint">Enter để gửi · Shift + Enter để xuống dòng</p>
        </aside>
      </main>
    </div>
  </>;
}
