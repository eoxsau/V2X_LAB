/* ============================================================ Simulation Chatbot
   시뮬레이션 탭 컨트롤 패널 옆 ✦ 아이콘에서 열리는 대화형 챗봇.
   탭의 "시나리오 어시스턴트"(전체 시나리오 1회 입력 → 적용, tab-scenario.jsx)와는
   별개 기능 — 여기는 컨트롤 패널의 옵션에 대해 궁금한 점을 물어보거나, 옵션 하나하나를
   패널에서 직접 클릭하기 귀찮을 때 대화로 사소하게 바꾸는 용도. scValidateField 등
   검증 로직은 tab-scenario.jsx에 정의된 것을 그대로 재사용한다(같은 전역 스코프).
   ============================================================ */
// 파일 첨부 버튼용 아이콘 — ui.jsx의 Icon 셋을 건드리지 않기 위해 이 파일 안에서 자체 정의
function ClipIcon({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5 12.5 20a4 4 0 0 1-5.7-5.7L15 6.1a2.6 2.6 0 0 1 3.7 3.7L10 18.5" />
    </svg>
  );
}

const SIMCHAT_KEY = 'v2x_sim_chat_v1';
function simChatLoadHistory() {
  try { return JSON.parse(localStorage.getItem(SIMCHAT_KEY) || '[]'); } catch { return []; }
}
function simChatSaveHistory(list) {
  try { localStorage.setItem(SIMCHAT_KEY, JSON.stringify(list.slice(-40))); } catch {}
}

const SIMCHAT_GREETING = {
  role: 'assistant',
  content: '안녕하세요! 시뮬레이션 옵션에 대해 물어보거나(예: "lookahead가 뭐야?"), 자연어로 설정 변경을 요청해보세요(예: "지연시간 알고리즘을 mec_aware_latency로 바꿔줘").',
};

function SimChatDiffChip({ field }) {
  const ok = field.valid;
  return (
    <span className="chat-diff-chip" style={{ background: ok ? 'var(--good-tint)' : 'var(--bad-tint)', color: ok ? 'var(--good)' : 'var(--bad)' }}>
      {ok ? <Icon.check size={10} /> : '✕'} {field.key}: {String(field.current ?? '—')} → {String(field.proposed)}
      {!ok && field.reason ? ` (${field.reason})` : ''}
    </span>
  );
}

function SimChatBubble({ msg }) {
  const cls = msg.role === 'user' ? 'user' : (msg.isError ? 'error' : 'assistant');
  return (
    <div className={'chat-bubble ' + cls}>
      <div>{msg.content}</div>
      {!!(msg.appliedKeys && msg.appliedKeys.length) && (
        <div className="row gap6" style={{ flexWrap: 'wrap', marginTop: 7 }}>
          {msg.appliedKeys.map((f, i) => <SimChatDiffChip key={i} field={f} />)}
        </div>
      )}
    </div>
  );
}

function SimulationChatPanel({ simConfig, setSimConfig }) {
  const [messages, setMessages] = useState(() => simChatLoadHistory());
  const [inputText, setInputText] = useState('');
  const [loading, setLoading] = useState(false);
  const [attachedFile, setAttachedFile] = useState(null);
  const fileInputRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, loading]);

  function handleFileChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setInputText(String(reader.result || ''));
      setAttachedFile({ name: file.name });
    };
    reader.readAsText(file);
    e.target.value = '';
  }

  async function send() {
    const text = inputText.trim();
    if (!text || loading) return;
    const userMsg = { role: 'user', content: text };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setInputText('');
    setAttachedFile(null);
    setLoading(true);

    try {
      const res = await fetch('http://127.0.0.1:8001/api/scenarios/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: nextMessages.map(m => ({ role: m.role, content: m.content })),
          current_config: simConfig || {},
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      const diff = data.diff || {};
      const rationale = data.rationale || {};

      const appliedKeys = [];
      const validUpdates = {};
      for (const section of ['cost_weights', 'algorithm_selection', 'policy_options']) {
        const sectionDiff = diff[section];
        if (!sectionDiff || typeof sectionDiff !== 'object') continue;
        for (const [key, value] of Object.entries(sectionDiff)) {
          const current = simConfig?.[section]?.[key];
          const check = scValidateField(section, key, value);
          appliedKeys.push({ section, key, current, proposed: value, ...check, reason_text: rationale[key] || rationale[`${section}.${key}`] || null });
          if (check.valid) {
            validUpdates[section] = validUpdates[section] || {};
            validUpdates[section][key] = value;
          }
        }
      }
      if (Object.keys(validUpdates).length > 0 && simConfig && setSimConfig) {
        const next = JSON.parse(JSON.stringify(simConfig));
        for (const section of Object.keys(validUpdates)) {
          next[section] = { ...next[section], ...validUpdates[section] };
        }
        setSimConfig(next);
      }

      const assistantMsg = { role: 'assistant', content: data.reply, appliedKeys };
      const withAssistant = [...nextMessages, assistantMsg];
      setMessages(withAssistant);
      simChatSaveHistory(withAssistant);
    } catch (e) {
      const errMsg = { role: 'assistant', content: e.message || '오류가 발생했습니다.', isError: true };
      const withErr = [...nextMessages, errMsg];
      setMessages(withErr);
      simChatSaveHistory(withErr);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const displayMessages = messages.length === 0 ? [SIMCHAT_GREETING] : messages;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
        {displayMessages.map((m, i) => <SimChatBubble key={i} msg={m} />)}
        {loading && <div className="chat-bubble assistant muted" style={{ fontSize: 11.5 }}>입력 중…</div>}
      </div>
      <div style={{ borderTop: '1px solid var(--border)', padding: 11, flex: '0 0 auto' }}>
        {attachedFile && (
          <div style={{ marginBottom: 7 }}>
            <Chip>
              {attachedFile.name}
              <span style={{ marginLeft: 6, cursor: 'pointer', opacity: 0.6 }} onClick={() => setAttachedFile(null)}>✕</span>
            </Chip>
          </div>
        )}
        <div className="row gap8" style={{ alignItems: 'flex-end' }}>
          <input ref={fileInputRef} type="file" accept=".json,.txt,.md,.py,.js,.ts,.yaml,.yml,.csv" style={{ display: 'none' }} onChange={handleFileChange} />
          <button className="btn icon sm" onClick={() => fileInputRef.current?.click()} title="파일 첨부"><ClipIcon size={13} /></button>
          <textarea
            className="input"
            style={{ flex: 1, height: 'auto', minHeight: 34, maxHeight: 120, fontFamily: 'var(--sans)', fontSize: 11.5, lineHeight: 1.5, padding: '7px 10px', resize: 'none' }}
            placeholder='예: "lookahead_k가 뭐야?" 또는 "지연시간 알고리즘을 mec_aware_latency로 바꿔줘"'
            value={inputText}
            onChange={e => setInputText(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="btn primary sm" disabled={loading || !inputText.trim()} onClick={send}>
            <Icon.spark size={13} /> 전송
          </button>
        </div>
      </div>
    </div>
  );
}
window.SimulationChatPanel = SimulationChatPanel;
