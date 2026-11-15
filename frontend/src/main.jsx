
import ReportDownload from './ReportDownload.jsx';
import React, {lazy, Suspense, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import MarkdownMessage from './MarkdownMessage.jsx';
import ResearchPagination from './ResearchPagination.jsx';
import {useResearchPage} from './useResearchPage.js';
import {request} from './api.js';
import {
  Activity, ArrowRight, Atom, Beaker, BookOpen, Check, Circle, CircleAlert,
  Download, FlaskConical, Hash, History, Link2, LoaderCircle, Menu, PanelRightClose,
  Play, Plus, Radio, RotateCcw, Send, Share2, Sparkles, Square, Users, X, Zap
} from 'lucide-react';
import './style.css';

const SourceGraph = lazy(() => import('./SourceGraph.jsx'));

const sessionKey = 'labthread.session';
const legacySessionKey = 'causalforge.session';

const initials = name => (name || '?')
  .split(/\s+/)
  .slice(0, 2)
  .map(x => x[0])
  .join('')
  .toUpperCase();

const time = value => new Intl.DateTimeFormat([], {
  hour: 'numeric',
  minute: '2-digit'
}).format(new Date(value));

const eventId = () => crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;

function Avatar({person, ai = false, size = 'md'}) {
  return (
    <span
      className={`avatar ${size} ${ai ? 'ai' : ''}`}
      style={!ai ? {background: person?.color || person?.sender_color} : undefined}
    >
      {ai
        ? <Atom size={size === 'sm' ? 14 : 17}/>
        : initials(person?.display_name || person?.sender_name)}
    </span>
  );
}

function StatusPill({connected}) {
  return (
    <span className={`status-pill ${connected ? 'online' : ''}`}>
      <i/>
      {connected ? 'Live' : 'Reconnecting'}
    </span>
  );
}

function RichText({children}) {
  const text = String(children || '');
  const parts = [];
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s]+)/g;

  let cursor = 0;
  let match;

  while ((match = pattern.exec(text))) {
    if (match.index > cursor) {
      parts.push(text.slice(cursor, match.index));
    }

    const url = match[2] || match[3];
    const label = match[1] || url;

    parts.push(
      <a
        key={`${match.index}-${url}`}
        href={url}
        target="_blank"
        rel="noreferrer"
      >
        {label}
      </a>
    );

    cursor = pattern.lastIndex;
  }

  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }

  return <span className="rich-text">{parts}</span>;
}

function SignIn({onReady}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError('');

    try {
      const session = await request('/session', {
        method: 'POST',
        body: JSON.stringify({display_name: name})
      });

      localStorage.setItem(sessionKey, JSON.stringify(session));
      onReady(session);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="signin-shell">
      <section className="signin-card">
        <div className="brand-mark">
          <Atom size={28}/>
        </div>

        <p className="kicker">LABTHREAD COLLABORATIVE LAB</p>

        <h1>
          Discover together.
          <br/>
          <em>Test what matters.</em>
        </h1>

        <p className="signin-copy">
          A shared research room where your team and an AI scientist
          turn questions into evidence—live.
        </p>

        <form onSubmit={submit}>
          <label>Your researcher name</label>

          <div className="name-entry">
            <input
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Maya Chen"
              minLength="2"
              maxLength="48"
            />

            <button disabled={busy || name.trim().length < 2}>
              {busy
                ? <LoaderCircle className="spin" size={18}/>
                : <ArrowRight size={18}/>}
            </button>
          </div>

          {error && <p className="form-error">{error}</p>}
        </form>

        <div className="signin-notes">
          <span><Radio size={14}/> WebSocket native</span>
          <span><FlaskConical size={14}/> Real simulations</span>
          <span><Users size={14}/> Shared rooms</span>
        </div>
      </section>

      <div className="signin-visual">
        <div className="orbit one"/>
        <div className="orbit two"/>
        <div className="orbit three"/>
        <div className="core"><Atom size={42}/></div>
        <span className="float-tag tag-one">HYPOTHESIS</span>
        <span className="float-tag tag-two">EVIDENCE</span>
        <span className="float-tag tag-three">DISCOVERY</span>
      </div>
    </main>
  );
}

function RoomDialog({mode, onClose, onSubmit, busy}) {
  const [title, setTitle] = useState('');
  const [roomId, setRoomId] = useState('');
  const [code, setCode] = useState('');

  function submit(e) {
    e.preventDefault();
    onSubmit(mode === 'create' ? {title} : {roomId, join_code: code});
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={e => e.target === e.currentTarget && onClose()}
    >
      <form className="modal" onSubmit={submit}>
        <button
          type="button"
          className="icon-button modal-close"
          onClick={onClose}
        >
          <X size={17}/>
        </button>

        <div className="modal-icon">
          {mode === 'create' ? <Plus/> : <Link2/>}
        </div>

        <h2>
          {mode === 'create'
            ? 'Create a research room'
            : 'Join a research room'}
        </h2>

        {mode !== 'create' && <p>Enter the room ID and invite code.</p>}

        {mode === 'create' ? (
          <label>
            Room name
            <input
              autoFocus
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="e.g. Urban mobility lab"
              required
              minLength="3"
            />
          </label>
        ) : (
          <>
            <label>
              Room ID
              <input
                autoFocus
                value={roomId}
                onChange={e => setRoomId(e.target.value)}
                placeholder="10-character room ID"
                required
              />
            </label>

            <label>
              Invite code
              <input
                value={code}
                onChange={e => setCode(e.target.value.toUpperCase())}
                placeholder="6-character code"
                required
                minLength="6"
              />
            </label>
          </>
        )}

        <button className="solid-button" disabled={busy}>
          {busy
            ? <LoaderCircle className="spin" size={16}/>
            : mode === 'create'
              ? <Plus size={16}/>
              : <ArrowRight size={16}/>}
          {' '}
          {mode === 'create' ? 'Create room' : 'Join room'}
        </button>
      </form>
    </div>
  );
}

function InviteDialog({room, onClose}) {
  const [copied, setCopied] = useState(false);

  const link = `${location.origin}${location.pathname}?invite=${encodeURIComponent(room.id)}&code=${encodeURIComponent(room.join_code)}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      const node = document.createElement('textarea');
      node.value = link;
      node.style.position = 'fixed';
      node.style.opacity = '0';
      document.body.appendChild(node);
      node.select();
      document.execCommand('copy');
      node.remove();
    }

    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={e => e.target === e.currentTarget && onClose()}
    >
      <section className="modal invite-modal">
        <button
          type="button"
          className="icon-button modal-close"
          onClick={onClose}
        >
          <X size={17}/>
        </button>

        <div className="modal-icon"><Share2/></div>

        <h2>Invite researchers</h2>

        <p>
          Anyone with this link can sign in and join
          {' '}<strong>{room.title}</strong>.
          Room membership is still verified by the server.
        </p>

        <label>
          Shareable invitation link
          <div className="invite-link-field">
            <input
              value={link}
              readOnly
              onFocus={e => e.target.select()}
            />

            <button onClick={copy}>
              {copied
                ? <Check size={15}/>
                : <Share2 size={15}/>}
              {' '}
              {copied ? 'Copied' : 'Copy link'}
            </button>
          </div>
        </label>

        <div className="invite-details">
          <span>ROOM <b>{room.id}</b></span>
          <span>CODE <b>{room.join_code}</b></span>
        </div>
        <label>Connect iMessage to this room
          <input readOnly value={`JOIN ${room.id} ${room.join_code}`} onFocus={e => e.target.select()}/>
          <small>Text this command to your assigned Photon number. Then text NAME followed by your name.</small>
        </label>
      </section>
    </div>
  );
}

function LeftSidebar({
  rooms, roomId, setRoomId, onDialog, session,
  experimentsPage, experimentCount, mobileOpen, setMobileOpen, onProfile, onLogout, onRoomAction
}) {
  const experiments = experimentsPage.items;

  return (
    <aside className={`left-sidebar ${mobileOpen ? 'mobile-open' : ''}`}>
      <div className="brand">
        <span className="brand-symbol"><Atom size={21}/></span>

        <div>
          <strong>Labthread</strong>
          <small>RESEARCH COMMONS</small>
        </div>

        <button
          className="icon-button mobile-close"
          onClick={() => setMobileOpen(false)}
        >
          <X size={18}/>
        </button>
      </div>

      <div className="workspace-label">
        <span>RESEARCH ROOMS</span>
        <button
          className="icon-button"
          onClick={() => onDialog('create')}
          title="Create room"
        >
          <Plus size={16}/>
        </button>
      </div>

      <nav className="room-list">
        {rooms.filter(room => !room.archived).map(room => (
          <button
            key={room.id}
            className={`room-row ${room.id === roomId ? 'active' : ''}`}
            onClick={() => {
              setRoomId(room.id);
              setMobileOpen(false);
            }}
          >
            <span className="room-glyph"><Hash size={15}/></span>

            <span>
              <strong>{room.title}</strong>
              <small>
                {room.last_message ||
                  `${room.member_count} researcher${room.member_count === 1 ? '' : 's'}`}
              </small>
            </span>

            {room.id === roomId && <i/>}
          </button>
        ))}
      </nav>

      <button className="join-button" onClick={() => onDialog('join')}>
        <Link2 size={15}/> Join with invite code
      </button>

      {rooms.find(room => room.id === roomId)?.role === 'owner' && <div className="room-actions">
        <button onClick={() => onRoomAction('archive', roomId)}>Archive room</button>
        <button onClick={() => onRoomAction('delete', roomId)}>Delete room</button>
      </div>}
      {rooms.filter(room => room.archived && room.role === 'owner').map(room =>
        <div className="room-actions" key={room.id}><span>{room.title} · Archived</span>
          <button onClick={() => onRoomAction('restore', room.id)}>Restore</button><button onClick={() => onRoomAction('delete', room.id)}>Delete</button></div>)}
      <div className="sidebar-rule"/>

      <div className="workspace-label">
        <span>PREVIOUS EXPERIMENTS</span>
        <History size={14}/>
      </div>

      <div className="past-experiments">
        {experiments.length ? (
          experiments.map(item => (
            <div className="past-row" key={item.id}>
              <span className={`past-icon ${item.status}`}>
                <Beaker size={14}/>
              </span>

              <div>
                <strong>{item.prompt}</strong>
                <small>{item.status.replaceAll('_', ' ')}</small>
              </div>
            </div>
          ))
        ) : (
          <p className="sidebar-empty">
            Experiments from this room will appear here.
          </p>
        )}
        <ResearchPagination page={experimentsPage} label="experiments" total={experimentCount}/>
      </div>

      <div className="profile">
        <Avatar person={session.participant} size="sm"/>
        <div>
          <strong>{session.participant.display_name}</strong>
          <small>Researcher</small>
        </div>
        <details className="profile-menu">
          <summary className="profile-toggle" aria-label="Account options"><Menu size={18}/></summary>
          <div className="profile-options">
            <button type="button" onClick={event => {
              event.currentTarget.closest('details').open = false;
              onProfile();
            }}>Edit name</button>
            <button type="button" onClick={event => {
              event.currentTarget.closest('details').open = false;
              onLogout();
            }}>Sign out</button>
          </div>
        </details>
      </div>
    </aside>
  );
}

function EmptyRooms({onCreate, onJoin}) {
  return (
    <section className="empty-rooms">
      <div className="empty-constellation">
        <Atom size={34}/>
        <i/><i/><i/>
      </div>

      <p className="kicker">YOUR COLLABORATIVE LAB</p>

      <h1>
        Start with a room,
        <br/>
        end with evidence.
      </h1>

      <p>
        Create a shared research space or join an existing investigation.
      </p>

      <div>
        <button className="solid-button" onClick={onCreate}>
          <Plus size={16}/> Create a room
        </button>

        <button className="soft-button" onClick={onJoin}>
          <Link2 size={16}/> Join a room
        </button>
      </div>
    </section>
  );
}

function ExperimentVisual({result}) {
  if (!result) return null;

  if (result.experiment_type === 'clt_monte_carlo') {
    return (
      <div className="clt-grid">
        {(result.summary || []).map(row => (
          <div key={row.sample_size}>
            <strong>n = {row.sample_size}</strong>
            <span>Variance <b>{row.empirical_variance.toFixed(4)}</b> / {row.theoretical_variance.toFixed(4)}</span>
            <span>Skewness <b>{row.empirical_skewness.toFixed(3)}</b> / {row.theoretical_skewness.toFixed(3)}</span>
            <small>KS {row.ks_statistic.toFixed(4)} · {row.simulations.toLocaleString()} means</small>
          </div>
        ))}
      </div>
    );
  }

  if (result.experiment_type === 'classification_comparison') {
    return (
      <div className="metric-grid">
        {(result.methods || []).map(method => (
          <div className="metric-method" key={method.method}>
            <strong>{method.method}</strong>
            <span><b>{(method.accuracy * 100).toFixed(1)}%</b> accuracy</span>
            <span><b>{(method.weighted_f1 * 100).toFixed(1)}%</b> weighted F1</span>
            <small>
              {method.cv_accuracy_mean != null
                ? `CV ${(method.cv_accuracy_mean * 100).toFixed(1)}% ± ${(method.cv_accuracy_std * 100).toFixed(1)}`
                : `${method.seed_count || 1} split${method.seed_count === 1 ? '' : 's'} · F1 SD ${((method.weighted_f1_std || 0) * 100).toFixed(2)}`}
            </small>
          </div>
        ))}
      </div>
    );
  }

  if (result.experiment_type === 'traffic_simulation') {
    const a = result.results.baseline.average_travel_time;
    const b = result.results.candidate.average_travel_time;
    const max = Math.max(a, b) * 1.12;

    return (
      <div className="traffic-result">
        <div className="network-diagram">
          <svg
            viewBox="0 0 260 130"
            role="img"
            aria-label="Four node traffic network"
          >
            <path d="M24 65 L96 23 L235 65 M24 65 L96 107 L235 65 M96 23 L96 107"/>
            <circle cx="24" cy="65" r="8"/>
            <circle cx="96" cy="23" r="8"/>
            <circle cx="96" cy="107" r="8"/>
            <circle cx="235" cy="65" r="8"/>
            <text x="12" y="90">START</text>
            <text x="218" y="90">END</text>
          </svg>
        </div>

        <div className="bar-comparison">
          <div>
            <span>Existing network</span>
            <i style={{width: `${a / max * 100}%`}}/>
            <b>{a} min</b>
          </div>

          <div className="candidate">
            <span>Road added</span>
            <i style={{width: `${b / max * 100}%`}}/>
            <b>{b} min</b>
          </div>

          <small>
            Equilibrium average travel time · lower is better
          </small>
        </div>
      </div>
    );
  }

  const a = result.results?.baseline?.sample_mmd2;
  const b = result.results?.candidate?.sample_mmd2;
  const max = Math.max(a || 0, b || 0) * 1.12 || 1;

  return (
    <div className="bar-comparison flow">
      <div>
        <span>Baseline MMD²</span>
        <i style={{width: `${a / max * 100}%`}}/>
        <b>{a}</b>
      </div>

      <div className="candidate">
        <span>Candidate MMD²</span>
        <i style={{width: `${b / max * 100}%`}}/>
        <b>{b}</b>
      </div>

      <small>Distribution error · lower is better</small>
    </div>
  );
}

function ExperimentCard({job, onRun, onCancel, onRerun, onDownload, activity}) {
  if (!job) return null;

  const result = job.results;
  const running = [
    'queued', 'researching', 'planning', 'generating', 'validating',
    'running', 'debugging', 'evaluating', 'reporting'
  ].includes(job.status);
  const autonomous = Boolean(job.autonomous);
  const progress = job.progress ?? activity?.progress ?? 0;
  const evidence = job.execution_evidence;

  return (
    <article className={`experiment-card ${result ? 'complete' : ''}`}>
      <header>
        <span>
          <FlaskConical size={16}/>
          {' '}
          {result ? 'EXPERIMENT RESULT' : autonomous ? 'AUTONOMOUS EXPERIMENT' : 'PROPOSED EXPERIMENT'}
        </span>
        <small>{job.id}</small>
      </header>

      <h3>{job.plan?.title || result?.experiment || job.prompt}</h3>

      {autonomous && evidence && (
        <span className={`verification-pill ${evidence.execution_verified ? 'verified' : 'unverified'}`}>
          {evidence.execution_verified ? <Check size={11}/> : <CircleAlert size={11}/>}
          {evidence.execution_verified ? 'Verified execution' : evidence.verification_status.replaceAll('_', ' ')}
        </span>
      )}

      {running && (
        <div className="progress-wrap">
          <div className="progress-copy">
            <span>
              {job.stage?.replaceAll('_', ' ') || activity?.message || 'Queued'}
            </span>
            <b>{progress}%</b>
          </div>
          <i>
            <span style={{width: `${progress}%`}}/>
          </i>
        </div>
      )}

      {result ? (
        <>
          <ExperimentVisual result={result}/>

          <p className="result-verdict">
            <Check size={15}/>
            {result.verdict}
          </p>

          <p className="limitations">
            Bounded model · {result.limitations?.slice(0, 2).join(' · ')}
          </p>

          {job.artifacts?.length > 0 && (
            <div className="artifact-list">
              {job.artifacts.map(artifact => (
                <button key={artifact.id} onClick={() => onDownload?.(job, artifact)}>
                  <Download size={12}/> {artifact.name}
                </button>
              ))}
            </div>
          )}

          {autonomous && (
            <button className="secondary-action" onClick={() => onRerun?.(job)}>
              <RotateCcw size={13}/> Rerun with new seed
            </button>
          )}
        </>
      ) : job.status === 'awaiting_approval' ? (
        <>
          <div className="experiment-spec">
            <span><b>CONTROL</b> Existing conditions</span>
            <ArrowRight size={15}/>
            <span><b>INTERVENTION</b> Proposed change</span>
          </div>

          <button className="run-button" onClick={onRun}>
            <Play size={15}/> Run experiment
          </button>
        </>
      ) : (
        <>
          <p className={`agent-working ${job.status === 'failed' ? 'failed' : ''}`}>
            {running ? <LoaderCircle className="spin" size={14}/> : <CircleAlert size={14}/>}
            {job.error || 'The research agent is preparing this study.'}
          </p>
          {running && autonomous && (
            <button className="secondary-action danger" onClick={() => onCancel?.(job)}>
              <Square size={12}/> Cancel safely
            </button>
          )}
          {job.status === 'failed' && autonomous && (
            <button className="secondary-action" onClick={() => onRerun?.(job)}>
              <RotateCcw size={13}/> Rerun as new version
            </button>
          )}
        </>
      )}

      {job.comparison?.metric_deltas?.length > 0 && (
        <div className="version-comparison">
          <strong>VERSION COMPARISON · SEED {job.comparison.previous_seed} → {job.comparison.current_seed}</strong>
          {job.comparison.metric_deltas.map(item => (
            <span key={item.method}>
              {item.method}: F1 {item.weighted_f1_delta >= 0 ? '+' : ''}
              {(item.weighted_f1_delta * 100).toFixed(2)} points
            </span>
          ))}
        </div>
      )}

      {autonomous && evidence && (
        <section className="execution-evidence">
          <header>
            <strong>EXECUTION EVIDENCE</strong>
            <span>{evidence.hashes_verified ? 'HASHES VERIFIED' : 'EVIDENCE INCOMPLETE'}</span>
          </header>
          <div>
            <span><b>Exit code</b>{evidence.exit_code ?? '—'}</span>
            <span><b>Runtime</b>{evidence.runtime_seconds != null ? `${evidence.runtime_seconds}s` : '—'}</span>
            <span><b>Seeds</b>{evidence.actual_seed_count ?? '—'} / {evidence.requested_seed_count || '—'}</span>
            <span><b>Training runs</b>{evidence.actual_training_runs ?? '—'} / {evidence.expected_training_runs ?? '—'}</span>
            <span><b>Simulations</b>{evidence.actual_simulations?.toLocaleString?.() ?? '—'} / {evidence.expected_simulations?.toLocaleString?.() ?? '—'}</span>
            <span><b>Artifacts</b>{evidence.artifact_filenames?.length || 0}</span>
          </div>
          <p><b>Started</b> {evidence.started_at || 'not recorded'}</p>
          <p><b>Completed</b> {evidence.completed_at || 'not recorded'}</p>
          <p className="script-path"><b>Code</b> {evidence.script_path || 'No executable script reached the worker.'}</p>
          {evidence.contract_checks?.some(item => !item.passed) && (
            <ul>
              {evidence.contract_checks.filter(item => !item.passed).map(item => (
                <li key={item.name}>{item.name}: expected {String(item.expected)}, got {String(item.actual)}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {autonomous && (job.events?.length > 0 || job.attempts?.length > 0) && (
        <details className="experiment-log">
          <summary>Execution log and provenance</summary>
          {job.events?.slice(-8).map(event => (
            <p key={event.event_id}>
              <b>{event.payload?.stage || event.event_type}</b>
              {event.payload?.message}
            </p>
          ))}
          {job.attempts?.flatMap(attempt => (attempt.logs || []).slice(-12).map((line, index) => (
            <code key={`${attempt.id}-${index}`}>{line}</code>
          )))}
        </details>
      )}
    </article>
  );
}

function Message({message, currentUser, job, onRun, onCancel, onRerun, onDownload, activity}) {
  const own = message.sender_id === currentUser.id;
  const agent = message.sender_type === 'agent';

  if (agent && ['experiment_proposal', 'experiment_result', 'experiment_job'].includes(message.kind)) {
    return (
      <div className="message-row agent-card-row">
        <Avatar ai/>

        <div className="message-stack">
          <div className="message-meta">
            <b>LangChain @chatbot</b>
            <span>{time(message.created_at)}</span>
          </div>

          <div className="agent-text">
            <MarkdownMessage>{message.content}</MarkdownMessage>
          </div>

          {(message.kind === 'experiment_result' || job?.status !== 'completed') && (
            <ExperimentCard
              job={job}
              onRun={onRun}
              onCancel={onCancel}
              onRerun={onRerun}
              onDownload={onDownload}
              activity={activity}
            />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`message-row ${own ? 'own' : ''}`}>
      <Avatar person={message} ai={agent}/>

      <div className="message-stack">
        <div className="message-meta">
          <b>
            {agent
              ? 'LangChain @chatbot'
              : own
                ? 'You'
                : message.sender_name}
          </b>
          <span>{time(message.created_at)}</span>
        </div>

        <div className={`bubble ${agent ? 'agent-bubble' : ''}`}>
          {agent
            ? <MarkdownMessage>{message.content}</MarkdownMessage>
            : <RichText>{message.content}</RichText>}
        </div>
      </div>
    </div>
  );
}

/*
 * CENTER CHAT
 *
 * Scrolling improvements:
 * - Users can manually scroll through older messages.
 * - Auto-scroll only occurs when near the bottom.
 * - AI activity updates do not force the user to the bottom.
 * - New rooms start at the latest messages.
 */

function CenterChat({
  snapshot, messages, session, connected, presence, typing,
  sendMessage, activity, onMenu, onRight, onInvite,
  onCancel, onRerun, onDownload
}) {
  const [draft, setDraft] = useState('');

  const scrollRef = useRef(null);
  const typingTimer = useRef(null);
  const shouldAutoScroll = useRef(true);
  const previousRoomId = useRef(snapshot?.id);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;

    const distanceFromBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight;

    shouldAutoScroll.current = distanceFromBottom < 120;
  }

  useEffect(() => {
    if (previousRoomId.current !== snapshot?.id) {
      previousRoomId.current = snapshot?.id;
      shouldAutoScroll.current = true;

      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    }
  }, [snapshot?.id]);

  useEffect(() => {
    const el = scrollRef.current;

    if (!el || !shouldAutoScroll.current) return;

    el.scrollTo({
      top: el.scrollHeight,
      behavior: 'auto'
    });
  }, [messages.length, typing.length, activity]);

  useEffect(() => {
    return () => {
      clearTimeout(typingTimer.current);
    };
  }, []);

  function submit(e) {
    e.preventDefault();

    if (!draft.trim()) return;

    sendMessage(draft.trim());
    setDraft('');
  }

  function change(e) {
    setDraft(e.target.value);

    sendMessage(null, 'typing.started');

    clearTimeout(typingTimer.current);

    typingTimer.current = setTimeout(() => {
      sendMessage(null, 'typing.stopped');
    }, 900);
  }

  const memberCount = snapshot?.members?.length ?? 0;

  return (
    <main className="chat-pane">
      <header className="room-header">
        <button className="icon-button mobile-menu" onClick={onMenu}>
          <Menu size={19}/>
        </button>

        <div>
          <div className="room-title-line">
            <h2>{snapshot?.title}</h2>
            <StatusPill connected={connected}/>
            <span className="agent-mode">{snapshot.agent_mode === 'deepagents' ? 'Deep Agents configured' : 'Demo mode'}</span>
          </div>

          <p>
            {memberCount} {memberCount === 1 ? 'researcher' : 'researchers'} · Shared AI assistant
          </p>
        </div>

        <div className="member-faces">
          {(presence.length ? presence : snapshot?.members || [])
            .slice(0, 4)
            .map(person => (
              <Avatar
                key={person.id}
                person={person}
                size="sm"
              />
            ))}
          <Avatar ai size="sm"/>
        </div>

        <button className="invite-chip" onClick={onInvite}>
          <Share2 size={14}/> Invite
        </button>

        <button className="icon-button mobile-right" onClick={onRight}>
          <PanelRightClose size={19}/>
        </button>
      </header>

      <div
        className="conversation"
        ref={scrollRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          overscrollBehaviorY: 'contain'
        }}
      >
        <div className="conversation-intro">
          <span><Atom size={22}/></span>

          <h1>{snapshot?.title}</h1>

          <div className="invite-code">
            <Hash size={13}/>
            {' '}
            {snapshot?.id}
            {' '}
            <i/>
            {' '}
            INVITE {snapshot?.join_code}
          </div>
        </div>

        {messages.map(message => (
          <Message
            key={message.id}
            message={message}
            currentUser={session.participant}
            job={
              message.job_id
                ? snapshot?.message_jobs?.[message.job_id]
                : null
            }
            onRun={() => sendMessage('@chatbot run the experiment.')}
            onCancel={onCancel}
            onRerun={onRerun}
            onDownload={onDownload}
            activity={activity}
          />
        ))}

        {activity?.kind === 'agent' && (
          <div className="agent-live">
            <Avatar ai size="sm"/>

            <span>
              <i/><i/><i/>
            </span>

            <p>{activity.message || 'Reviewing evidence…'}</p>
          </div>
        )}

        {typing.length > 0 && (
          <div className="typing-line">
            {typing.map(x => x.display_name).join(', ')}
            {' '}
            {typing.length === 1 ? 'is' : 'are'} typing…
          </div>
        )}
      </div>

      <form className="composer" onSubmit={submit}>
        <div className="composer-box">
          <textarea
            rows="1"
            value={draft}
            onChange={change}
            placeholder="Message the room, or ask @chatbot…"
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit(e);
              }
            }}
          />

          <button disabled={!draft.trim() || !connected}>
            <Send size={17}/>
          </button>

          <div className="composer-hint">
            <Sparkles size={13}/>
            {' '}
            Mention @chatbot to search papers or propose an experiment.
            <span>Shift + Enter for a new line</span>
          </div>
        </div>
      </form>
    </main>
  );
}

function RightSidebar({snapshot, session, open, onClose, sendMessage, activity, onCancel, onRerun}) {
  const [tab, setTab] = useState('context');
  const [graphVisited, setGraphVisited] = useState(false);
  const scope = {roomId: snapshot.id, token: session.token, revision: snapshot.research_revision};
  const sourcesPage = useResearchPage({...scope, kind: 'sources', enabled: tab === 'sources'});
  const hypothesesPage = useResearchPage({...scope, kind: 'hypotheses', enabled: tab === 'context'});
  const discoveriesPage = useResearchPage({...scope, kind: 'discoveries', enabled: tab === 'context'});
  const active = snapshot.active_experiment;
  const sources = sourcesPage.items;
  const hypotheses = hypothesesPage.items;
  const discoveries = discoveriesPage.items;
  const counts = snapshot.research_counts || {};

  function selectTab(value) {
    if (value === 'graph') setGraphVisited(true);
    setTab(value);
  }

  function handleTabKey(event, current) {
    const tabs = ['context', 'sources', 'graph'];
    let next;
    if (event.key === 'ArrowRight') next = tabs[(tabs.indexOf(current) + 1) % tabs.length];
    else if (event.key === 'ArrowLeft') next = tabs[(tabs.indexOf(current) + tabs.length - 1) % tabs.length];
    else if (event.key === 'Home') next = tabs[0];
    else if (event.key === 'End') next = tabs[tabs.length - 1];
    if (next) {
      event.preventDefault();
      selectTab(next);
      document.getElementById(`${next}-tab`)?.focus();
    }
  }

  return (
    <aside className={`right-sidebar ${open ? 'mobile-open' : ''}`}>
      <header>
        <div>
          <h2>Research context</h2>
        </div>

        <button
          className="icon-button mobile-close"
          onClick={onClose}
        >
          <X size={18}/>
        </button>
      </header>

      <div className="right-tabs" role="tablist" aria-label="Research views">
        <button
          role="tab" id="context-tab" aria-controls="context-panel" aria-selected={tab === 'context'}
          tabIndex={tab === 'context' ? 0 : -1} onKeyDown={event => handleTabKey(event, 'context')}
          className={tab === 'context' ? 'active' : ''}
          onClick={() => selectTab('context')}
        >
          Context
        </button>

        <button
          role="tab" id="sources-tab" aria-controls="sources-panel" aria-selected={tab === 'sources'}
          tabIndex={tab === 'sources' ? 0 : -1} onKeyDown={event => handleTabKey(event, 'sources')}
          className={tab === 'sources' ? 'active' : ''}
          onClick={() => selectTab('sources')}
        >
          Sources <span>{counts.sources || 0}</span>
        </button>

        <button role="tab" id="graph-tab" aria-controls="graph-panel" aria-selected={tab === 'graph'}
          tabIndex={tab === 'graph' ? 0 : -1} onKeyDown={event => handleTabKey(event, 'graph')}
          className={tab === 'graph' ? 'active' : ''}
          onClick={() => selectTab('graph')}>
          Graph
        </button>
      </div>

      {tab === 'context' ? (
        <div className="right-scroll" role="tabpanel" id="context-panel" aria-labelledby="context-tab">
          <section className="context-section">
            <div className="section-heading">
              <span>
                <Activity size={14}/> ACTIVE EXPERIMENT
              </span>

              {active && (
                <small className={active.status}>
                  {active.status.replaceAll('_', ' ')}
                </small>
              )}
            </div>

            {active ? (
              <div className="mini-experiment">
                <strong>
                  {active.results?.experiment || active.prompt}
                </strong>

                {active.status === 'awaiting_approval' && (
                  <button
                    onClick={() => sendMessage('@chatbot run the experiment.')}
                  >
                    <Play size={13}/> Run now
                  </button>
                )}

                {[
                  'queued', 'researching', 'planning', 'generating', 'validating',
                  'running', 'debugging', 'evaluating', 'reporting'
                ].includes(active.status) && (
                  <div className="mini-progress">
                    <i>
                      <span style={{width: `${active.progress ?? activity?.progress ?? 0}%`}}/>
                    </i>

                    <small>
                      {active.stage?.replaceAll('_', ' ') || activity?.message || 'Experiment running'}
                    </small>
                    {active.autonomous && (
                      <button onClick={() => onCancel(active)}>
                        <Square size={11}/> Cancel
                      </button>
                    )}
                  </div>
                )}

                {active.results && (
                  <>
                    <p>
                      <Check size={13}/>
                      {active.results.verdict}
                    </p>
                    {active.autonomous && (
                      <button onClick={() => onRerun(active)}>
                        <RotateCcw size={11}/> Rerun
                      </button>
                    )}
                  </>
                )}

                {active.execution_evidence && (
                  <div className={`mini-evidence ${active.execution_evidence.execution_verified ? 'verified' : ''}`}>
                    <strong>
                      {active.execution_evidence.execution_verified ? '✓ Verified worker execution' : '⚠ Execution not fully verified'}
                    </strong>
                    <small>
                      Exit {active.execution_evidence.exit_code ?? '—'} · {active.execution_evidence.runtime_seconds ?? '—'}s · {' '}
                      {active.execution_evidence.actual_training_runs ?? active.execution_evidence.actual_simulations ?? '—'} runs
                    </small>
                  </div>
                )}
              </div>
            ) : (
              <p className="context-empty">
                Ask @chatbot to suggest an experiment when you are ready.
              </p>
            )}
          </section>

          <section className="context-section">
            <div className="section-heading">
              <span>
                <Zap size={14}/> HYPOTHESES
              </span>
              <small>{counts.hypotheses || 0}</small>
            </div>

            {hypotheses.map((item, index) => (
              <div className="hypothesis" key={item.id}>
                <span>
                  H{String(index + 1).padStart(2, '0')}
                </span>

                <div>
                  <strong>{item.title}</strong>
                  <p>{item.description}</p>
                </div>

                <Circle size={10}/>
              </div>
            ))}

            <ResearchPagination page={hypothesesPage} label="hypotheses" total={counts.hypotheses}/>

            {!hypotheses.length && !hypothesesPage.loading && !hypothesesPage.error && (
              <p className="context-empty">
                Testable hypotheses will be pinned here.
              </p>
            )}
          </section>

          <section className="context-section">
            <div className="section-heading">
              <span>
                <Sparkles size={14}/> DISCOVERIES
              </span>
              <small>{counts.discoveries || 0}</small>
            </div>

            {discoveries.map(item => (
              <div className="discovery" key={item.job_id}>
                <Check size={14}/>

                <div>
                  <strong>{item.title}</strong>
                  <p>{item.detail}</p>
                </div>
              </div>
            ))}

            <ResearchPagination page={discoveriesPage} label="discoveries" total={counts.discoveries}/>

            {!discoveries.length && !discoveriesPage.loading && !discoveriesPage.error && (
              <p className="context-empty">
                Completed experiment results appear here.
              </p>
            )}
          </section>
        </div>
      ) : tab === 'sources' ? (
        <div className="right-scroll source-list" role="tabpanel" id="sources-panel" aria-labelledby="sources-tab">
          {sources.map((source, index) => (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              key={source.url || index}
            >
              <span>
                {String(index + 1).padStart(2, '0')}
              </span>

              <div>
                <strong>{source.title}</strong>
                <p>{source.reason || source.evidence_level}</p>
                <small>
                  {source.evidence_level || 'OpenAlex metadata'}
                </small>
              </div>

              <ArrowRight size={14}/>
            </a>
          ))}

          <ResearchPagination page={sourcesPage} label="sources" total={counts.sources}/>

          {!sources.length && !sourcesPage.loading && !sourcesPage.error && (
            <p className="context-empty padded">
              @chatbot will add clickable research sources here.
            </p>
          )}
        </div>
      ) : null}

      {graphVisited && <div className="graph-tab-panel" role="tabpanel" id="graph-panel"
        aria-labelledby="graph-tab" hidden={tab !== 'graph'}>
        <Suspense fallback={<div className="graph-module-loading" role="status">
          <LoaderCircle className="spin" size={20}/> Loading graph…
        </div>}>
          <SourceGraph roomId={snapshot.id} token={session.token} revision={snapshot.research_revision}
            active={tab === 'graph'} drawerOpen={open}/>
        </Suspense>
      </div>}

      <footer>
        {snapshot.active_experiment?.status === 'completed' && <ReportDownload roomId={snapshot.id} jobId={snapshot.active_experiment.id} token={session.token}/>}
        <BookOpen size={14}/>
        {tab === 'graph' ? 'Trace queries, shared sources, and citations' : 'Sources link to original publications'}
      </footer>
    </aside>
  );
}

function App() {
  const [session, setSession] = useState(() => {
    try {
      const current = localStorage.getItem(sessionKey);
      const legacy = localStorage.getItem(legacySessionKey);
      if (current === null && legacy !== null) localStorage.setItem(sessionKey, legacy);
      if (legacy !== null) localStorage.removeItem(legacySessionKey);
      return JSON.parse(current ?? legacy);
    } catch {
      return null;
    }
  });

  const [rooms, setRooms] = useState([]);
  const [roomId, setRoomId] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [messages, setMessages] = useState([]);
  const currentSnapshot = snapshot?.id === roomId ? snapshot : null;
  const experimentsPage = useResearchPage({roomId, token: session?.token, kind: 'experiments',
    revision: currentSnapshot?.research_revision, enabled: Boolean(currentSnapshot)});


  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const [connected, setConnected] = useState(false);
  const [presence, setPresence] = useState([]);
  const [typing, setTyping] = useState([]);
  const [activity, setActivity] = useState(null);

  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);

  const socketRef = useRef(null);
  const reconnectRef = useRef();
  const sequenceRef = useRef(0);
  const selectedRoomRef = useRef(roomId);
  selectedRoomRef.current = roomId;

  async function loadRooms(preferred) {
    if (!session) return;

    try {
      const list = await request('/rooms', {token: session.token});
      setRooms(list);

      const available=list.filter(room => !room.archived);
      const chosen =
        preferred ||
        (roomId && available.some(x => x.id === roomId)
          ? roomId
          : available[0]?.id);

      setRoomId(chosen || null);
    } catch (err) {
      if (/session/i.test(err.message)) {
        localStorage.removeItem(sessionKey);
        setSession(null);
      } else {
        setError(err.message);
      }
    }
  }

  useEffect(() => {
    if (!session) return;

    const params = new URLSearchParams(location.search);
    const invite = params.get('invite');
    const code = params.get('code');

    if (invite && code) {
      request(`/rooms/${invite}/join`, {
        token: session.token,
        method: 'POST',
        body: JSON.stringify({join_code: code})
      })
        .then(room => {
          history.replaceState({}, '', location.pathname);
          return loadRooms(room.id);
        })
        .catch(err => {
          history.replaceState({}, '', location.pathname);
          setError(`Invitation could not be joined: ${err.message}`);
          loadRooms();
        });
    } else {
      loadRooms();
    }
  }, [session]);

  useEffect(() => {
    if (!roomId || !session) return;

    const controller = new AbortController();
    request(`/rooms/${roomId}`, {token: session.token, signal: controller.signal})
      .then(data => {
        if (controller.signal.aborted || selectedRoomRef.current !== roomId) return;
        setSnapshot(current => current?.id === data.id && current.latest_sequence > data.latest_sequence
          ? current : data);
        setMessages(data.messages || []);
        sequenceRef.current = data.latest_sequence || 0;
      })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [roomId, session]);

  useEffect(() => {
    if (!roomId || !session) return;

    let stopped = false;
    let attempt = 0;

    function refreshSnapshot() {
      request(`/rooms/${roomId}`, {token: session.token})
        .then(data => {
          if (stopped || selectedRoomRef.current !== roomId) return;
          setSnapshot(current => current?.id === data.id && current.latest_sequence > data.latest_sequence
            ? current : data);
        })
        .catch(() => {});
    }

    function apply(event) {
      if (stopped || selectedRoomRef.current !== roomId) return;
      if (event.sequence) {
        sequenceRef.current = Math.max(
          sequenceRef.current,
          event.sequence
        );
      }

      if (event.type === 'message.history') {
        if (event.payload.messages) {
          setMessages(event.payload.messages);
        }

        for (const replayed of event.payload.events || []) {
          apply(replayed);
        }
      } else if (event.type === 'message.created') {
        const message = event.payload.message;

        setMessages(current =>
          current.some(x => x.id === message.id)
            ? current
            : [...current, message]
        );

        if (message.job_id || message.sender_type === 'agent') {
          refreshSnapshot();
        }
      } else if (event.type === 'presence.updated') {
        setPresence(event.payload.online || []);
      } else if (
        event.type === 'typing.started' &&
        event.payload.participant.id !== session.participant.id
      ) {
        setTyping(current =>
          current.some(x => x.id === event.payload.participant.id)
            ? current
            : [...current, event.payload.participant]
        );
      } else if (event.type === 'typing.stopped') {
        setTyping(current =>
          current.filter(x => x.id !== event.payload.participant.id)
        );
      } else if (
        event.type === 'agent.started' ||
        event.type === 'agent.thinking' ||
        event.type === 'agent.tool_started' ||
        event.type === 'agent.tool_completed'
      ) {
        setActivity({
          kind: 'agent',
          message: event.payload.message || event.payload.summary,
          job_id: event.payload.job_id
        });
      } else if (event.type === 'experiment.progress') {
        setActivity({
          kind: 'experiment',
          ...event.payload
        });
      } else if (event.type === 'experiment.stage_changed') {
        setActivity({kind: 'experiment', ...event.payload});
        request(`/rooms/${roomId}`, {token: session.token})
          .then(setSnapshot)
          .catch(() => {});
      } else if ([
        'experiment.proposed',
        'experiment.started',
        'experiment.queued',
        'experiment.research_completed',
        'experiment.artifact_created',
        'experiment.validation_failed',
        'experiment.repair_generated',
        'experiment.verification_completed',
        'experiment.cancelled',
        'experiment.completed',
        'experiment.failed',
        'agent.completed',
        'participant.updated',
        'room.join',
        'room.leave'
      ].includes(event.type)) {
        if ([
          'agent.completed',
          'experiment.cancelled',
          'experiment.completed',
          'experiment.failed'
        ].includes(event.type)) {
          setActivity(null);
        }

        refreshSnapshot();
      }
    }

    function connect() {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';

      const ws = new WebSocket(
        `${scheme}://${location.host}/ws/rooms/${roomId}?token=${encodeURIComponent(session.token)}&since=${sequenceRef.current}`
      );

      socketRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
      };

      ws.onmessage = e => {
        try {
          apply(JSON.parse(e.data));
        } catch {}
      };

      ws.onclose = event => {
        if (event.code === 4403) {
          stopped = true;
          setSnapshot(null); setRoomId(null);
          loadRooms();
        }
        setConnected(false);

        if (!stopped) {
          reconnectRef.current = setTimeout(
            connect,
            Math.min(500 * 2 ** attempt++, 8000)
          );
        }
      };

      ws.onerror = () => ws.close();
    }

    connect();

    return () => {
      stopped = true;
      clearTimeout(reconnectRef.current);
      socketRef.current?.close();
      setConnected(false);
      setPresence([]);
      setTyping([]);
    };
  }, [roomId, session]);

  function sendMessage(content, type = 'message.send') {
    const ws = socketRef.current;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError('The room is reconnecting. Your message was not sent.');
      return;
    }

    ws.send(JSON.stringify({
      type,
      event_id: eventId(),
      room_id: 'ignored-by-server',
      timestamp: new Date().toISOString(),
      payload: content ? {content} : {}
    }));
  }

  async function editProfile() {
    const name = window.prompt('Your display name', session.participant.display_name);
    if (!name) return;
    try {
      const participant = await request('/me', {token: session.token, method: 'PATCH', body: JSON.stringify({display_name: name})});
      const next = {...session, participant};
      localStorage.setItem(sessionKey, JSON.stringify(next));
      setSession(next);
    } catch (err) { setError(err.message); }
  }

  async function signOut() {
    try {
      await request('/logout', {token: session.token, method: 'POST'});
      localStorage.removeItem(sessionKey);
      setRoomId(null); setSnapshot(null); setSession(null);
    } catch (err) { setError(err.message); }
  }

  async function roomAction(action, id) {
    if (action !== 'restore' && !window.confirm(action === 'delete' ? 'Permanently delete this room, its messages and research?' : 'Archive this room? It will become unavailable to all members.')) return;
    try {
      await request(`/rooms/${id}${action === 'delete' ? '' : '/' + action}`,
        {token: session.token, method: action === 'delete' ? 'DELETE' : 'POST'});
      setSnapshot(null); setRoomId(null);
      const list = await request('/rooms', {token: session.token});
      setRooms(list);
      setRoomId(list.find(room => !room.archived && room.id !== id)?.id || null);
    } catch (err) { setError(err.message); }
  }

  async function refreshRoom() {
    if (!roomId || !session) return;
    const data = await request(`/rooms/${roomId}`, {token: session.token});
    setSnapshot(data);
  }

  async function experimentAction(job, action) {
    try {
      await request(`/experiments/${job.id}/${action}`, {
        token: session.token,
        method: 'POST'
      });
      await refreshRoom();
    } catch (err) {
      setError(err.message);
    }
  }

  async function downloadArtifact(job, artifact) {
    try {
      const response = await fetch(
        `${API}/experiments/${job.id}/artifacts/${artifact.id}`,
        {headers: {Authorization: `Bearer ${session.token}`}}
      );
      if (!response.ok) throw new Error('Artifact download failed');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = artifact.name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    }
  }

  async function submitDialog(data) {
    setBusy(true);
    setError('');

    try {
      let room;

      if (dialog === 'create') {
        room = await request('/rooms', {
          token: session.token,
          method: 'POST',
          body: JSON.stringify(data)
        });
      } else {
        room = await request(`/rooms/${data.roomId}/join`, {
          token: session.token,
          method: 'POST',
          body: JSON.stringify({join_code: data.join_code})
        });
      }

      setDialog(null);
      await loadRooms(room.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return <SignIn onReady={setSession}/>;
  }

  return (
    <div className="app-shell">
      <LeftSidebar
        rooms={rooms}
        onProfile={editProfile}
        onLogout={signOut}
        onRoomAction={roomAction}
        roomId={roomId}
        setRoomId={setRoomId}
        onDialog={setDialog}
        session={session}
        experimentsPage={experimentsPage}
        experimentCount={currentSnapshot?.research_counts?.experiments || 0}
        mobileOpen={leftOpen}
        setMobileOpen={setLeftOpen}
      />

      {!roomId ? (
        <EmptyRooms
          onCreate={() => setDialog('create')}
          onJoin={() => setDialog('join')}
        />
      ) : currentSnapshot ? (
        <CenterChat
          snapshot={currentSnapshot}
          messages={messages}
          session={session}
          connected={connected}
          presence={presence}
          typing={typing}
          sendMessage={sendMessage}
          activity={activity}
          onMenu={() => setLeftOpen(true)}
          onRight={() => setRightOpen(true)}
          onInvite={() => setInviteOpen(true)}
          onCancel={job => experimentAction(job, 'cancel')}
          onRerun={job => experimentAction(job, 'rerun')}
          onDownload={downloadArtifact}
        />
      ) : (
        <div className="loading-room">
          <LoaderCircle className="spin"/>
          Loading room…
        </div>
      )}

      {roomId && currentSnapshot && (
        <RightSidebar
          key={roomId}
          session={session}
          snapshot={currentSnapshot}
          open={rightOpen}
          onClose={() => setRightOpen(false)}
          sendMessage={sendMessage}
          activity={activity}
          onCancel={job => experimentAction(job, 'cancel')}
          onRerun={job => experimentAction(job, 'rerun')}
        />
      )}

      {dialog && (
        <RoomDialog
          mode={dialog}
          onClose={() => setDialog(null)}
          onSubmit={submitDialog}
          busy={busy}
        />
      )}

      {inviteOpen && currentSnapshot && (
        <InviteDialog
          room={currentSnapshot}
          onClose={() => setInviteOpen(false)}
        />
      )}

      {error && (
        <div className="toast">
          <CircleAlert size={17}/>
          <span>{error}</span>

          <button onClick={() => setError('')}>
            <X size={15}/>
          </button>
        </div>
      )}

      <div
        className={`mobile-scrim ${(leftOpen || rightOpen) ? 'show' : ''}`}
        onClick={() => {
          setLeftOpen(false);
          setRightOpen(false);
        }}
      />
    </div>
  );
}

class AppErrorBoundary extends React.Component {
  state = {failed: false};

  static getDerivedStateFromError() {
    return {failed: true};
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="loading-room" role="alert">
        The workspace could not render.
        <button type="button" onClick={() => window.location.reload()}>Reload workspace</button>
      </main>
    );
  }
}

createRoot(document.getElementById('root')).render(
  <AppErrorBoundary><App/></AppErrorBoundary>
);
