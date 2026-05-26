use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    env, fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Sender},
        Arc, Mutex,
    },
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, State};

const EVENT_CHANNEL: &str = "agent://event";
const TERMINAL_EVENT_CHANNEL: &str = "terminal://event";
const RPC_TIMEOUT: Duration = Duration::from_secs(240);

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct HostStatus {
    runtime_transport: &'static str,
    event_channel: &'static str,
    runtime_running: bool,
    repo_root: String,
    python_module: &'static str,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionCreatePayload {
    workspace_id: String,
    title: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceMemoryClearPayload {
    workspace_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceMemoryInitPayload {
    workspace_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFocusUpdatePayload {
    workspace_id: String,
    focus: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MessageSendPayload {
    session_id: String,
    content: String,
    attachments: Vec<String>,
    task_id: Option<String>,
    mode: Option<String>,
    new_task: Option<bool>,
    background: Option<bool>,
    client_message_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MessageListPayload {
    session_id: String,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskGetPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskControlPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskListPayload {
    session_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeIdPayload {
    worktree_id: String,
    full: Option<bool>,
    include_full_diff: Option<bool>,
    max_diff_bytes: Option<u64>,
    diff_preview_bytes: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeMergePayload {
    worktree_id: String,
    approved: Option<bool>,
    approval_id: Option<String>,
    target_branch: Option<String>,
    verification_commands: Option<Vec<String>>,
    verification_timeout_ms: Option<u64>,
    review_status: Option<String>,
    reviewer_summary: Option<String>,
    reviewer: Option<String>,
    multi_agent_worktree_strategy: Option<Value>,
    diff_preview_bytes: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeByTaskPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorktreeCleanupPayload {
    worktree_id: String,
    force: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskCreatePayload {
    name: String,
    prompt: String,
    schedule: String,
    enabled: Option<bool>,
    status: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskUpdatePayload {
    task_id: String,
    name: Option<String>,
    prompt: Option<String>,
    schedule: Option<String>,
    enabled: Option<bool>,
    status: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskTogglePayload {
    task_id: String,
    enabled: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskIdPayload {
    task_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScheduledTaskLogsPayload {
    task_id: Option<String>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ApprovalSubmitPayload {
    approval_id: String,
    decision: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommandLogGetPayload {
    command_id: String,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct CommandLogListPayload {
    task_id: Option<String>,
    session_id: Option<String>,
    status: Option<String>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommandCancelPayload {
    command_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DiffGetPayload {
    patch_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileListPayload {
    workspace_root: String,
    path: Option<String>,
    max_entries: Option<usize>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileReadPayload {
    workspace_root: String,
    path: String,
    max_bytes: Option<usize>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct TerminalStartPayload {
    cwd: Option<String>,
    shell: Option<String>,
    cols: Option<u16>,
    rows: Option<u16>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TerminalWritePayload {
    terminal_id: String,
    data: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TerminalResizePayload {
    terminal_id: String,
    cols: u16,
    rows: u16,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TerminalStopPayload {
    terminal_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GitLocalPayload {
    cwd: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GitLocalDiffPayload {
    cwd: String,
    path: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GitLocalCheckoutPayload {
    cwd: String,
    branch: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GitLocalCommitPayload {
    cwd: String,
    message: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileEntryView {
    name: String,
    path: String,
    kind: String,
    size: Option<u64>,
    modified_at: Option<u128>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TraceListPayload {
    task_id: String,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct LogExportPayload {
    session_id: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct ErrorsListPayload {
    session_id: Option<String>,
    task_id: Option<String>,
    source: Option<String>,
    limit: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct MetricsListPayload {
    session_id: Option<String>,
    limit: Option<u64>,
}

#[derive(Clone, Default)]
struct RuntimeManager {
    bridge: Arc<Mutex<RuntimeBridge>>,
}

#[derive(Default)]
struct TerminalManager {
    sessions: Mutex<HashMap<String, TerminalSession>>,
    next_id: AtomicU64,
}

struct TerminalSession {
    id: String,
    cwd: String,
    shell: String,
    started_at: u128,
    writer: Box<dyn Write + Send>,
    backend: TerminalBackend,
}

enum TerminalBackend {
    Pty {
        master: Box<dyn MasterPty + Send>,
        child: Box<dyn portable_pty::Child + Send + Sync>,
    },
    Process {
        child: Child,
    },
}

#[derive(Default)]
struct RuntimeBridge {
    process: Option<RuntimeProcess>,
    next_request_id: AtomicU64,
}

struct RuntimeProcess {
    _child: Child,
    stdin: ChildStdin,
    pending: Arc<Mutex<HashMap<String, Sender<Result<Value, String>>>>>,
}

impl RuntimeManager {
    fn call(&self, app_handle: &AppHandle, method: &str, params: Value) -> Result<Value, String> {
        // Phase 1: ensure the runtime is started (holds lock briefly)
        {
            let mut bridge = self
                .bridge
                .lock()
                .map_err(|_| "Failed to acquire runtime bridge lock".to_string())?;
            bridge.ensure_started(app_handle)?;
        }

        // Phase 2: send request and register pending callback (lock per-operation)
        let (rx, request_id, pending) = {
            let mut bridge = self
                .bridge
                .lock()
                .map_err(|_| "Failed to acquire runtime bridge lock".to_string())?;

            let request_id = format!(
                "req_{}",
                bridge.next_request_id.fetch_add(1, Ordering::Relaxed)
            );
            let (tx, rx) = mpsc::channel();

            let process = bridge
                .process
                .as_mut()
                .ok_or_else(|| "Runtime process is not available".to_string())?;

            process
                .pending
                .lock()
                .map_err(|_| "Failed to lock pending response map".to_string())?
                .insert(request_id.clone(), tx);

            let payload = serde_json::to_string(&json!({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }))
            .map_err(|reason| format!("Failed to serialize RPC request: {reason}"))?;

            if let Err(reason) =
                writeln!(process.stdin, "{payload}").and_then(|_| process.stdin.flush())
            {
                let _ = process
                    .pending
                    .lock()
                    .map(|mut pending| pending.remove(&request_id));
                return Err(format!("Failed to write RPC request to runtime: {reason}"));
            }

            (rx, request_id, Arc::clone(&process.pending))
        };

        match rx.recv_timeout(RPC_TIMEOUT) {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(message)) => Err(message),
            Err(_) => {
                let _ = pending
                    .lock()
                    .map(|mut pending_guard| pending_guard.remove(&request_id));
                Err("Timed out while waiting for the Python runtime".to_string())
            }
        }
    }

    async fn call_async(
        &self,
        app_handle: AppHandle,
        method: String,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let manager = self.clone();
        tauri::async_runtime::spawn_blocking(move || manager.call(&app_handle, &method, params))
            .await
            .map_err(|reason| format!("Runtime async worker failed: {reason}"))?
    }

    fn runtime_running(&self) -> bool {
        self.bridge
            .lock()
            .map(|bridge| bridge.process.is_some())
            .unwrap_or(false)
    }
}

impl RuntimeBridge {
    fn ensure_started(&mut self, app_handle: &AppHandle) -> Result<(), String> {
        if self.process.is_some() {
            return Ok(());
        }

        let data_dir = resolve_data_dir(app_handle)?;
        fs::create_dir_all(&data_dir)
            .map_err(|reason| format!("Failed to create data directory: {reason}"))?;

        let runtime_src = resolve_runtime_src()?;
        let database_path = env::var_os("LOCAL_AGENT_DB_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| data_dir.join("data.db"));
        let working_dir = resolve_working_dir()?;
        let python_executable =
            env::var("LOCAL_AGENT_PYTHON").unwrap_or_else(|_| "python".to_string());

        let mut command = Command::new(python_executable);
        command
            .arg("-u")
            .arg("-m")
            .arg("local_agent_runtime.main")
            .current_dir(&working_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("PYTHONPATH", append_path_env("PYTHONPATH", &runtime_src)?)
            .env("LOCAL_AGENT_DB_PATH", database_path.as_os_str())
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1");

        let mut child = command
            .spawn()
            .map_err(|reason| format!("Failed to start Python runtime: {reason}"))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Python runtime stdin is not available".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Python runtime stdout is not available".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "Python runtime stderr is not available".to_string())?;

        let pending = Arc::new(Mutex::new(HashMap::new()));
        spawn_stdout_pump(app_handle.clone(), stdout, Arc::clone(&pending));
        spawn_stderr_pump(stderr);

        self.process = Some(RuntimeProcess {
            _child: child,
            stdin,
            pending,
        });

        Ok(())
    }
}

impl TerminalManager {
    fn next_terminal_id(&self) -> String {
        format!("term_{}", self.next_id.fetch_add(1, Ordering::Relaxed))
    }

    fn session_view(session: &TerminalSession, status: &str) -> Value {
        json!({
            "id": session.id,
            "cwd": session.cwd,
            "shell": session.shell,
            "status": status,
            "startedAt": session.started_at,
        })
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default()
}

fn default_terminal_shell() -> String {
    if let Ok(shell) = env::var("LOCAL_AGENT_TERMINAL_SHELL") {
        if !shell.trim().is_empty() {
            return shell;
        }
    }
    if cfg!(target_os = "windows") {
        "powershell.exe".to_string()
    } else {
        env::var("SHELL").unwrap_or_else(|_| "bash".to_string())
    }
}

fn terminal_shell_command(shell: &str) -> CommandBuilder {
    let normalized = shell.trim();
    let lower = normalized.to_ascii_lowercase();
    if cfg!(target_os = "windows") {
        if lower == "powershell" || lower == "powershell.exe" {
            let mut command = CommandBuilder::new("powershell.exe");
            command.arg("-NoLogo");
            command.arg("-NoProfile");
            command.arg("-ExecutionPolicy");
            command.arg("Bypass");
            return command;
        }
        if lower == "pwsh" || lower == "pwsh.exe" {
            let mut command = CommandBuilder::new("pwsh.exe");
            command.arg("-NoLogo");
            command.arg("-NoProfile");
            return command;
        }
        if lower == "cmd" || lower == "cmd.exe" {
            return CommandBuilder::new("cmd.exe");
        }
    }

    CommandBuilder::new(if normalized.is_empty() {
        default_terminal_shell()
    } else {
        normalized.to_string()
    })
}

fn terminal_cwd(payload_cwd: Option<String>) -> Result<PathBuf, String> {
    let cwd = payload_cwd
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let resolved = cwd
        .canonicalize()
        .map_err(|reason| format!("Failed to resolve terminal working directory: {reason}"))?;
    if !resolved.is_dir() {
        return Err("Terminal working directory is not a directory".to_string());
    }
    Ok(normalize_windows_verbatim_path(&resolved))
}

fn normalize_windows_verbatim_path(path: &Path) -> PathBuf {
    if !cfg!(target_os = "windows") {
        return path.to_path_buf();
    }

    let value = path.to_string_lossy();
    if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = value.strip_prefix(r"\\?\") {
        return PathBuf::from(rest);
    }
    path.to_path_buf()
}

fn emit_terminal_event(app_handle: &AppHandle, payload: Value) {
    let _ = app_handle.emit(TERMINAL_EVENT_CHANNEL, payload);
}

fn spawn_terminal_reader(
    app_handle: AppHandle,
    terminal_id: String,
    mut reader: Box<dyn Read + Send>,
) {
    thread::spawn(move || {
        let mut buffer = [0_u8; 8192];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) => {
                    emit_terminal_event(
                        &app_handle,
                        json!({
                            "terminalId": terminal_id,
                            "kind": "exit",
                            "exitCode": null,
                            "ts": now_ms(),
                        }),
                    );
                    break;
                }
                Ok(size) => {
                    let chunk = String::from_utf8_lossy(&buffer[..size]).to_string();
                    emit_terminal_event(
                        &app_handle,
                        json!({
                            "terminalId": terminal_id,
                            "kind": "output",
                            "chunk": chunk,
                            "ts": now_ms(),
                        }),
                    );
                }
                Err(reason) => {
                    emit_terminal_event(
                        &app_handle,
                        json!({
                            "terminalId": terminal_id,
                            "kind": "error",
                            "message": format!("Terminal output failed: {reason}"),
                            "ts": now_ms(),
                        }),
                    );
                    break;
                }
            }
        }
    });
}

fn spawn_pipe_terminal(
    app_handle: &AppHandle,
    state: &TerminalManager,
    cwd_path: &Path,
    cwd: &str,
    shell: &str,
) -> Result<Value, String> {
    let mut command = if cfg!(target_os = "windows") {
        let lower = shell.trim().to_ascii_lowercase();
        if lower == "cmd" || lower == "cmd.exe" {
            let mut command = Command::new("cmd.exe");
            command.arg("/d");
            command.arg("/q");
            command.arg("/k");
            command
        } else {
            let executable = if lower == "pwsh" || lower == "pwsh.exe" {
                "pwsh.exe"
            } else {
                "powershell.exe"
            };
            let mut command = Command::new(executable);
            command.arg("-NoLogo");
            command.arg("-NoProfile");
            command.arg("-ExecutionPolicy");
            command.arg("Bypass");
            command.arg("-NoExit");
            command.arg("-Command");
            command.arg(
                "Remove-Module PSReadLine -ErrorAction SilentlyContinue; \
                 [Console]::InputEncoding = [System.Text.UTF8Encoding]::new(); \
                 [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new();",
            );
            command
        }
    } else {
        Command::new(shell)
    };

    command
        .current_dir(cwd_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("TERM", if cfg!(target_os = "windows") { "dumb" } else { "xterm-256color" });

    let mut child = command
        .spawn()
        .map_err(|reason| format!("Failed to start terminal shell: {reason}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "Terminal stdin is not available".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Terminal stdout is not available".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "Terminal stderr is not available".to_string())?;

    let terminal_id = state.next_terminal_id();
    let started_at = now_ms();
    let session = TerminalSession {
        id: terminal_id.clone(),
        cwd: cwd.to_string(),
        shell: shell.to_string(),
        started_at,
        writer: Box::new(stdin),
        backend: TerminalBackend::Process { child },
    };
    let terminal_view = TerminalManager::session_view(&session, "running");
    state
        .sessions
        .lock()
        .map_err(|_| "Failed to lock terminal sessions".to_string())?
        .insert(terminal_id.clone(), session);

    spawn_terminal_reader(app_handle.clone(), terminal_id.clone(), Box::new(stdout));
    spawn_terminal_reader(app_handle.clone(), terminal_id.clone(), Box::new(stderr));

    emit_terminal_event(
        app_handle,
        json!({
            "terminalId": terminal_id,
            "kind": "output",
            "chunk": format!("Started {shell} in {cwd}\r\n"),
            "cwd": cwd,
            "shell": shell,
            "ts": now_ms(),
        }),
    );

    Ok(json!({ "terminal": terminal_view }))
}

#[tauri::command]
fn terminal_start(
    app_handle: AppHandle,
    state: State<'_, TerminalManager>,
    payload: TerminalStartPayload,
) -> Result<Value, String> {
    let cwd_path = terminal_cwd(payload.cwd)?;
    let cwd = cwd_path.display().to_string();
    let shell = payload.shell.unwrap_or_else(default_terminal_shell);
    let cols = payload.cols.unwrap_or(100).clamp(20, 240);
    let rows = payload.rows.unwrap_or(30).clamp(6, 80);

    if cfg!(target_os = "windows") {
        return spawn_pipe_terminal(&app_handle, &state, &cwd_path, &cwd, &shell);
    }

    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|reason| format!("Failed to open PTY: {reason}"))?;

    let mut command = terminal_shell_command(&shell);
    command.cwd(&cwd_path);
    if cfg!(target_os = "windows") {
        command.env("TERM", "dumb");
    } else {
        command.env("TERM", "xterm-256color");
    }

    let child = pair
        .slave
        .spawn_command(command)
        .map_err(|reason| format!("Failed to start terminal shell: {reason}"))?;
    drop(pair.slave);

    let reader = pair
        .master
        .try_clone_reader()
        .map_err(|reason| format!("Failed to clone terminal reader: {reason}"))?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|reason| format!("Failed to open terminal writer: {reason}"))?;

    let terminal_id = state.next_terminal_id();
    let started_at = now_ms();
    let session = TerminalSession {
        id: terminal_id.clone(),
        cwd: cwd.clone(),
        shell: shell.clone(),
        started_at,
        writer,
        backend: TerminalBackend::Pty {
            master: pair.master,
            child,
        },
    };
    let terminal_view = TerminalManager::session_view(&session, "running");
    state
        .sessions
        .lock()
        .map_err(|_| "Failed to lock terminal sessions".to_string())?
        .insert(terminal_id.clone(), session);

    emit_terminal_event(
        &app_handle,
        json!({
            "terminalId": terminal_id,
            "kind": "output",
            "chunk": format!("Started {shell} in {cwd}\r\n"),
            "cwd": cwd,
            "shell": shell,
            "ts": now_ms(),
        }),
    );

    let reader_terminal_id = terminal_view
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    spawn_terminal_reader(app_handle.clone(), reader_terminal_id, reader);

    Ok(json!({ "terminal": terminal_view }))
}

#[tauri::command]
fn terminal_write(
    state: State<'_, TerminalManager>,
    payload: TerminalWritePayload,
) -> Result<Value, String> {
    let mut sessions = state
        .sessions
        .lock()
        .map_err(|_| "Failed to lock terminal sessions".to_string())?;
    let session = sessions
        .get_mut(&payload.terminal_id)
        .ok_or_else(|| "Terminal session was not found".to_string())?;
    session
        .writer
        .write_all(payload.data.as_bytes())
        .and_then(|_| session.writer.flush())
        .map_err(|reason| format!("Failed to write to terminal: {reason}"))?;
    Ok(json!({ "terminal": TerminalManager::session_view(session, "running") }))
}

#[tauri::command]
fn terminal_resize(
    state: State<'_, TerminalManager>,
    payload: TerminalResizePayload,
) -> Result<Value, String> {
    let sessions = state
        .sessions
        .lock()
        .map_err(|_| "Failed to lock terminal sessions".to_string())?;
    let session = sessions
        .get(&payload.terminal_id)
        .ok_or_else(|| "Terminal session was not found".to_string())?;
    let rows = payload.rows.clamp(6, 120);
    let cols = payload.cols.clamp(20, 300);
    if let TerminalBackend::Pty { master, .. } = &session.backend {
        master
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|reason| format!("Failed to resize terminal: {reason}"))?;
    }
    Ok(json!({ "terminal": TerminalManager::session_view(session, "running") }))
}

#[tauri::command]
fn terminal_stop(
    app_handle: AppHandle,
    state: State<'_, TerminalManager>,
    payload: TerminalStopPayload,
) -> Result<Value, String> {
    let mut session = state
        .sessions
        .lock()
        .map_err(|_| "Failed to lock terminal sessions".to_string())?
        .remove(&payload.terminal_id)
        .ok_or_else(|| "Terminal session was not found".to_string())?;
    match &mut session.backend {
        TerminalBackend::Pty { child, .. } => {
            let _ = child.kill();
        }
        TerminalBackend::Process { child } => {
            let _ = child.kill();
        }
    }
    let terminal_view = TerminalManager::session_view(&session, "exited");
    emit_terminal_event(
        &app_handle,
        json!({
            "terminalId": session.id,
            "kind": "exit",
            "exitCode": null,
            "ts": now_ms(),
        }),
    );
    Ok(json!({ "terminal": terminal_view }))
}

fn resolve_existing_dir(raw_cwd: &str) -> Result<PathBuf, String> {
    let cwd = PathBuf::from(raw_cwd.trim());
    if raw_cwd.trim().is_empty() {
        return Err("Working directory is required".to_string());
    }
    let resolved = cwd
        .canonicalize()
        .map_err(|reason| format!("Failed to resolve working directory: {reason}"))?;
    if !resolved.is_dir() {
        return Err("Working directory is not a directory".to_string());
    }
    Ok(resolved)
}

fn run_git(cwd: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .map_err(|reason| format!("Failed to start git: {reason}"))
}

fn run_git_success(cwd: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    let output = run_git(cwd, args)?;
    if output.status.success() {
        return Ok(output);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Err(if stderr.is_empty() { stdout } else { stderr })
}

fn git_stdout(cwd: &Path, args: &[&str]) -> Result<String, String> {
    let output = run_git_success(cwd, args)?;
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn resolve_git_repo(cwd: &Path) -> Result<PathBuf, String> {
    let root = git_stdout(cwd, &["rev-parse", "--show-toplevel"])?;
    let root = PathBuf::from(root.trim());
    if !root.is_dir() {
        return Err("Git repository root was not found".to_string());
    }
    Ok(root)
}

fn parse_status_file(raw: &str) -> Value {
    let status = raw.get(0..2).unwrap_or("").trim().to_string();
    let path = raw
        .get(3..)
        .unwrap_or("")
        .trim()
        .trim_matches('"')
        .to_string();
    json!({
        "path": path,
        "status": if status.is_empty() { "M".to_string() } else { status },
        "raw": raw,
    })
}

fn parse_branch_header(header: &str) -> (String, Option<String>, u64, u64) {
    let text = header.trim().strip_prefix("## ").unwrap_or(header.trim());
    let mut branch = text.to_string();
    let mut upstream = None;
    let mut ahead = 0_u64;
    let mut behind = 0_u64;

    if let Some((left, right)) = text.split_once("...") {
        branch = left.to_string();
        let mut remote = right.to_string();
        if let Some((remote_name, flags)) = right.split_once(' ') {
            remote = remote_name.to_string();
            for part in flags.trim_matches(|ch| ch == '[' || ch == ']').split(',') {
                let trimmed = part.trim();
                if let Some(value) = trimmed.strip_prefix("ahead ") {
                    ahead = value.parse().unwrap_or(0);
                }
                if let Some(value) = trimmed.strip_prefix("behind ") {
                    behind = value.parse().unwrap_or(0);
                }
            }
        }
        upstream = Some(remote);
    }

    (branch, upstream, ahead, behind)
}

fn git_status_value(cwd: &Path) -> Result<Value, String> {
    let repo_root = resolve_git_repo(cwd)?;
    let raw_status = git_stdout(cwd, &["status", "--short", "--branch"])?;
    let mut lines = raw_status.lines();
    let header = lines.next().unwrap_or("## HEAD");
    let (branch, upstream, ahead, behind) = parse_branch_header(header);
    let files: Vec<Value> = lines
        .filter(|line| !line.trim().is_empty())
        .map(parse_status_file)
        .collect();
    let branch_raw = git_stdout(cwd, &["branch", "--format=%(HEAD)%09%(refname:short)"])?;
    let branches: Vec<Value> = branch_raw
        .lines()
        .filter_map(|line| {
            let (head, name) = line.split_once('\t')?;
            let trimmed = name.trim();
            if trimmed.is_empty() {
                return None;
            }
            Some(json!({
                "name": trimmed,
                "current": head.trim() == "*",
            }))
        })
        .collect();
    let dirty_files = files.len();

    Ok(json!({
        "cwd": cwd.display().to_string(),
        "repoRoot": repo_root.display().to_string(),
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "dirtyFiles": dirty_files,
        "files": files,
        "branches": branches,
        "clean": dirty_files == 0,
        "rawStatus": raw_status,
    }))
}

fn is_safe_branch_name(branch: &str) -> bool {
    let trimmed = branch.trim();
    !trimmed.is_empty()
        && !trimmed.starts_with('-')
        && trimmed.len() <= 180
        && trimmed
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '/' | '_' | '-' | '.'))
}

fn changed_files_from_diff(diff: &str) -> Vec<String> {
    diff.lines()
        .filter_map(|line| {
            if !line.starts_with("diff --git ") {
                return None;
            }
            let parts: Vec<&str> = line.split_whitespace().collect();
            let path = parts.get(3).or_else(|| parts.get(2))?;
            Some(
                path.trim_start_matches("b/")
                    .trim_start_matches("a/")
                    .to_string(),
            )
        })
        .collect()
}

#[tauri::command]
fn git_local_status(payload: GitLocalPayload) -> Result<Value, String> {
    let cwd = resolve_existing_dir(&payload.cwd)?;
    git_status_value(&cwd)
}

#[tauri::command]
fn git_local_diff(payload: GitLocalDiffPayload) -> Result<Value, String> {
    let cwd = resolve_existing_dir(&payload.cwd)?;
    let repo_root = resolve_git_repo(&cwd)?;
    let mut args = vec!["diff", "--no-ext-diff", "--"];
    if let Some(path) = payload
        .path
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        args.push(path);
    }
    let diff = git_stdout(&cwd, &args)?;
    let mut stat_args = vec!["diff", "--stat", "--"];
    if let Some(path) = payload
        .path
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        stat_args.push(path);
    }
    let stat = git_stdout(&cwd, &stat_args)?;
    let truncated = diff.len() > 240_000;
    let diff_view = if truncated {
        diff.chars().take(240_000).collect::<String>()
    } else {
        diff.clone()
    };

    Ok(json!({
        "cwd": cwd.display().to_string(),
        "repoRoot": repo_root.display().to_string(),
        "diff": diff_view,
        "stat": stat,
        "files": changed_files_from_diff(&diff),
        "truncated": truncated,
    }))
}

#[tauri::command]
fn git_local_init(payload: GitLocalPayload) -> Result<Value, String> {
    let cwd = resolve_existing_dir(&payload.cwd)?;
    let output = run_git_success(&cwd, &["init"])?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let repo_root = resolve_git_repo(&cwd)?;
    Ok(json!({
        "cwd": cwd.display().to_string(),
        "repoRoot": repo_root.display().to_string(),
        "stdout": stdout,
        "stderr": stderr,
        "status": git_status_value(&cwd)?,
    }))
}

#[tauri::command]
fn git_local_checkout(payload: GitLocalCheckoutPayload) -> Result<Value, String> {
    let cwd = resolve_existing_dir(&payload.cwd)?;
    let repo_root = resolve_git_repo(&cwd)?;
    let branch = payload.branch.trim().to_string();
    if !is_safe_branch_name(&branch) {
        return Err("Branch name is invalid".to_string());
    }
    let output = run_git_success(&cwd, &["checkout", branch.as_str()])?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    Ok(json!({
        "cwd": cwd.display().to_string(),
        "repoRoot": repo_root.display().to_string(),
        "branch": branch,
        "stdout": stdout,
        "stderr": stderr,
        "status": git_status_value(&cwd)?,
    }))
}

#[tauri::command]
fn git_local_commit(payload: GitLocalCommitPayload) -> Result<Value, String> {
    let cwd = resolve_existing_dir(&payload.cwd)?;
    let repo_root = resolve_git_repo(&cwd)?;
    let message = payload.message.trim().to_string();
    if message.is_empty() {
        return Err("Commit message is required".to_string());
    }
    run_git_success(&cwd, &["add", "-A"])?;
    let output = run_git_success(&cwd, &["commit", "-m", message.as_str()])?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    Ok(json!({
        "cwd": cwd.display().to_string(),
        "repoRoot": repo_root.display().to_string(),
        "stdout": stdout,
        "stderr": stderr,
        "status": git_status_value(&cwd)?,
    }))
}

fn spawn_stdout_pump(
    app_handle: AppHandle,
    stdout: ChildStdout,
    pending: Arc<Mutex<HashMap<String, Sender<Result<Value, String>>>>>,
) {
    thread::spawn(move || {
        let reader = BufReader::new(stdout);

        for line_result in reader.lines() {
            let line = match line_result {
                Ok(line) => line,
                Err(reason) => {
                    eprintln!("Failed to read runtime stdout: {reason}");
                    break;
                }
            };

            if line.trim().is_empty() {
                continue;
            }

            let payload: Value = match serde_json::from_str(&line) {
                Ok(payload) => payload,
                Err(reason) => {
                    eprintln!("Failed to parse runtime stdout payload: {reason}");
                    continue;
                }
            };

            if payload.get("kind").and_then(Value::as_str) == Some("event") {
                if let Some(event_payload) = payload.get("payload").cloned() {
                    let _ = app_handle.emit(EVENT_CHANNEL, event_payload);
                }
                continue;
            }

            let Some(request_id) = payload.get("id").and_then(Value::as_str) else {
                continue;
            };

            let result = if let Some(error) = payload.get("error") {
                Err(error
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("Runtime returned an unknown error")
                    .to_string())
            } else {
                Ok(payload.get("result").cloned().unwrap_or(Value::Null))
            };

            if let Ok(mut pending_requests) = pending.lock() {
                if let Some(sender) = pending_requests.remove(request_id) {
                    let _ = sender.send(result);
                }
            }
        }
    });
}

fn spawn_stderr_pump(stderr: ChildStderr) {
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line_result in reader.lines() {
            match line_result {
                Ok(line) if !line.trim().is_empty() => eprintln!("runtime stderr: {line}"),
                Ok(_) => {}
                Err(reason) => {
                    eprintln!("Failed to read runtime stderr: {reason}");
                    break;
                }
            }
        }
    });
}

fn repo_root() -> Result<PathBuf, String> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .ok_or_else(|| {
            "Failed to resolve repository root from Cargo manifest directory".to_string()
        })
}

/// Resolve the data directory for database storage.
///
/// In packaged mode (no valid repo root), uses the OS-standard app data dir
/// provided by Tauri (e.g. `%APPDATA%\<app>` on Windows).
/// In dev mode, falls back to `repo_root()/runtime/.local-agent-runtime.sqlite3`
/// only if the repo root is available.
fn resolve_data_dir(app_handle: &AppHandle) -> Result<PathBuf, String> {
    match app_handle.path().app_data_dir() {
        Ok(dir) => Ok(dir),
        Err(reason) => {
            // Fallback: try repo_root for dev mode
            repo_root()
                .map(|root| root.join("runtime"))
                .map_err(|_| format!("Failed to resolve data directory: {reason}"))
        }
    }
}

fn resolve_skills_dir(app_handle: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(codex_home) = env::var("CODEX_HOME") {
        let trimmed = codex_home.trim();
        if !trimmed.is_empty() {
            return Ok(PathBuf::from(trimmed).join("skills"));
        }
    }

    match app_handle.path().home_dir() {
        Ok(dir) => Ok(dir.join(".codex").join("skills")),
        Err(reason) => Err(format!("Failed to resolve home directory: {reason}")),
    }
}

/// Resolve the runtime source directory for PYTHONPATH.
///
/// In dev mode uses `repo_root()/runtime/src`.
/// In packaged mode the Python package is installed, so PYTHONPATH can be empty.
fn resolve_runtime_src() -> Result<PathBuf, String> {
    match repo_root() {
        Ok(root) => Ok(root.join("runtime").join("src")),
        Err(_) => Ok(PathBuf::new()),
    }
}

/// Resolve the working directory for the Python process.
///
/// In dev mode uses `repo_root()`.
/// In packaged mode uses the app data directory.
fn resolve_working_dir() -> Result<PathBuf, String> {
    repo_root().or_else(|_| {
        // Packaged mode: use a temp-like dir as working dir
        env::current_dir().map_err(|e| format!("Failed to resolve working dir: {e}"))
    })
}

fn open_path_in_file_manager(path: &PathBuf) -> Result<(), String> {
    let mut command = if cfg!(target_os = "windows") {
        let mut command = Command::new("explorer.exe");
        command.arg(path);
        command
    } else if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(path);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(path);
        command
    };

    command
        .spawn()
        .map_err(|reason| format!("Failed to open {}: {reason}", path.display()))?;
    Ok(())
}

fn relative_path_string(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .ok()
        .map(|relative| {
            relative
                .components()
                .map(|component| component.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join("/")
        })
        .unwrap_or_default()
}

fn resolve_workspace_path(
    workspace_root: &str,
    relative_path: Option<&str>,
) -> Result<(PathBuf, PathBuf), String> {
    let root = PathBuf::from(workspace_root)
        .canonicalize()
        .map_err(|reason| format!("Failed to resolve workspace root: {reason}"))?;
    let raw_relative = relative_path.unwrap_or("").trim();
    let target = if raw_relative.is_empty() || raw_relative == "." {
        root.clone()
    } else {
        let relative = PathBuf::from(raw_relative);
        if relative.is_absolute() {
            return Err("Workspace file paths must be relative to the workspace root".to_string());
        }
        root.join(relative)
            .canonicalize()
            .map_err(|reason| format!("Failed to resolve workspace path: {reason}"))?
    };
    if target != root && !target.starts_with(&root) {
        return Err("Workspace path is outside the selected workspace".to_string());
    }
    Ok((root, target))
}

fn file_modified_at_ms(metadata: &fs::Metadata) -> Option<u128> {
    metadata
        .modified()
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_millis())
}

fn append_path_env(name: &str, first_path: &Path) -> Result<std::ffi::OsString, String> {
    let mut paths = vec![first_path.to_path_buf()];
    if let Some(existing) = env::var_os(name) {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).map_err(|reason| format!("Failed to prepare {name}: {reason}"))
}

#[tauri::command]
fn host_status(state: State<'_, RuntimeManager>) -> Result<HostStatus, String> {
    let root = repo_root()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "(packaged)".to_string());
    Ok(HostStatus {
        runtime_transport: "json-rpc-stdio",
        event_channel: EVENT_CHANNEL,
        runtime_running: state.runtime_running(),
        repo_root: root,
        python_module: "local_agent_runtime.main",
    })
}

#[tauri::command]
async fn workspace_open(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    path: String,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "workspace.open".to_string(),
            json!({ "path": path }),
        )
        .await
}

#[tauri::command]
async fn workspace_memory_clear(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceMemoryClearPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "workspace.memory.clear".to_string(),
            json!({ "workspaceId": payload.workspace_id }),
        )
        .await
}

#[tauri::command]
async fn workspace_memory_init(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceMemoryInitPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "workspace.memory.init".to_string(),
            json!({ "workspaceId": payload.workspace_id }),
        )
        .await
}

#[tauri::command]
async fn workspace_focus_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorkspaceFocusUpdatePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "workspace.focus.update".to_string(),
            json!({ "workspaceId": payload.workspace_id, "focus": payload.focus }),
        )
        .await
}

#[tauri::command]
async fn session_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionCreatePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "session.create".to_string(),
            json!({
                "workspaceId": payload.workspace_id,
                "title": payload.title,
            }),
        )
        .await
}

#[tauri::command]
async fn session_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "session.list".to_string(), json!({}))
        .await
}

#[tauri::command]
async fn message_send(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageSendPayload,
) -> Result<Value, String> {
    let is_supplement = payload.mode.as_deref() == Some("supplement");
    let background = payload.background.unwrap_or(!is_supplement);
    state
        .call_async(
            app_handle,
            "message.send".to_string(),
            json!({
                "sessionId": payload.session_id,
                "content": payload.content,
                "attachments": payload.attachments,
                "taskId": payload.task_id,
                "mode": payload.mode,
                "newTask": payload.new_task,
                "background": background,
                "clientMessageId": payload.client_message_id,
            }),
        )
        .await
}

#[tauri::command]
async fn message_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: MessageListPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "message.list".to_string(),
            json!({ "sessionId": payload.session_id, "limit": payload.limit }),
        )
        .await
}

#[tauri::command]
fn workspace_file_list(payload: WorkspaceFileListPayload) -> Result<Value, String> {
    let (root, target) = resolve_workspace_path(&payload.workspace_root, payload.path.as_deref())?;
    if !target.is_dir() {
        return Err("Workspace path is not a directory".to_string());
    }

    let limit = payload.max_entries.unwrap_or(200).clamp(1, 500);
    let mut entries = Vec::new();
    let mut truncated = false;
    for entry_result in
        fs::read_dir(&target).map_err(|reason| format!("Failed to read directory: {reason}"))?
    {
        let entry =
            entry_result.map_err(|reason| format!("Failed to read directory entry: {reason}"))?;
        if entries.len() >= limit {
            truncated = true;
            break;
        }
        let metadata = entry
            .metadata()
            .map_err(|reason| format!("Failed to read file metadata: {reason}"))?;
        let kind = if metadata.is_dir() {
            "directory"
        } else {
            "file"
        };
        entries.push(WorkspaceFileEntryView {
            name: entry.file_name().to_string_lossy().to_string(),
            path: relative_path_string(&root, &entry.path()),
            kind: kind.to_string(),
            size: if metadata.is_file() {
                Some(metadata.len())
            } else {
                None
            },
            modified_at: file_modified_at_ms(&metadata),
        });
    }
    entries.sort_by(|left, right| {
        let left_rank = if left.kind == "directory" { 0 } else { 1 };
        let right_rank = if right.kind == "directory" { 0 } else { 1 };
        left_rank
            .cmp(&right_rank)
            .then_with(|| left.name.to_lowercase().cmp(&right.name.to_lowercase()))
    });

    Ok(json!({
        "rootPath": root.display().to_string(),
        "path": relative_path_string(&root, &target),
        "entries": entries,
        "truncated": truncated,
    }))
}

#[tauri::command]
fn workspace_file_read(payload: WorkspaceFileReadPayload) -> Result<Value, String> {
    let (root, target) = resolve_workspace_path(&payload.workspace_root, Some(&payload.path))?;
    if !target.is_file() {
        return Err("Workspace path is not a file".to_string());
    }

    let limit = payload.max_bytes.unwrap_or(64 * 1024).clamp(1, 256 * 1024);
    let mut file =
        fs::File::open(&target).map_err(|reason| format!("Failed to open file: {reason}"))?;
    let mut buffer = Vec::new();
    std::io::Read::by_ref(&mut file)
        .take((limit + 1) as u64)
        .read_to_end(&mut buffer)
        .map_err(|reason| format!("Failed to read file: {reason}"))?;
    let truncated = buffer.len() > limit;
    if truncated {
        buffer.truncate(limit);
    }
    let bytes = buffer.len();
    if buffer.iter().any(|byte| *byte == 0) {
        return Ok(json!({
            "rootPath": root.display().to_string(),
            "path": relative_path_string(&root, &target),
            "bytes": bytes,
            "truncated": truncated,
            "binary": true,
        }));
    }

    match String::from_utf8(buffer) {
        Ok(content) => Ok(json!({
            "rootPath": root.display().to_string(),
            "path": relative_path_string(&root, &target),
            "content": content,
            "bytes": bytes,
            "truncated": truncated,
            "binary": false,
            "encoding": "utf-8",
        })),
        Err(_) => Ok(json!({
            "rootPath": root.display().to_string(),
            "path": relative_path_string(&root, &target),
            "bytes": bytes,
            "truncated": truncated,
            "binary": true,
        })),
    }
}

#[tauri::command]
async fn task_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskGetPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "task.get".to_string(),
            json!({ "taskId": payload.task_id }),
        )
        .await
}

#[tauri::command]
async fn task_cancel(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "task.cancel".to_string(),
            json!({ "taskId": payload.task_id }),
        )
        .await
}

#[tauri::command]
async fn task_pause(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "task.pause".to_string(),
            json!({ "taskId": payload.task_id }),
        )
        .await
}

#[tauri::command]
async fn task_resume(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskControlPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "task.resume".to_string(),
            json!({ "taskId": payload.task_id }),
        )
        .await
}

#[tauri::command]
async fn task_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<TaskListPayload>,
) -> Result<Value, String> {
    let params = match payload.and_then(|payload| payload.session_id) {
        Some(session_id) => json!({ "sessionId": session_id }),
        None => json!({}),
    };
    state
        .call_async(app_handle, "task.list".to_string(), params)
        .await
}

#[tauri::command]
async fn worktree_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeIdPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.get".to_string(),
            json!({ "worktreeId": payload.worktree_id }),
        )
        .await
}

#[tauri::command]
async fn worktree_get_by_task(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeByTaskPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.getByTask".to_string(),
            json!({ "taskId": payload.task_id }),
        )
        .await
}

#[tauri::command]
async fn worktree_status(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeIdPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.status".to_string(),
            json!({ "worktreeId": payload.worktree_id }),
        )
        .await
}

#[tauri::command]
async fn worktree_diff(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeIdPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.diff".to_string(),
            json!({
                "worktreeId": payload.worktree_id,
                "full": payload.full,
                "includeFullDiff": payload.include_full_diff,
                "maxDiffBytes": payload.max_diff_bytes,
                "diffPreviewBytes": payload.diff_preview_bytes,
            }),
        )
        .await
}

#[tauri::command]
async fn worktree_merge(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeMergePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.merge".to_string(),
            json!({
                "worktreeId": payload.worktree_id,
                "approved": payload.approved.unwrap_or(false),
                "approvalId": payload.approval_id,
                "targetBranch": payload.target_branch,
                "verificationCommands": payload.verification_commands,
                "verificationTimeoutMs": payload.verification_timeout_ms,
                "reviewStatus": payload.review_status,
                "reviewerSummary": payload.reviewer_summary,
                "reviewer": payload.reviewer,
                "multiAgentWorktreeStrategy": payload.multi_agent_worktree_strategy,
                "diffPreviewBytes": payload.diff_preview_bytes,
            }),
        )
        .await
}

#[tauri::command]
async fn worktree_request_merge_approval(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeMergePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.requestMergeApproval".to_string(),
            json!({
                "worktreeId": payload.worktree_id,
                "targetBranch": payload.target_branch,
                "verificationCommands": payload.verification_commands,
                "verificationTimeoutMs": payload.verification_timeout_ms,
                "reviewStatus": payload.review_status,
                "reviewerSummary": payload.reviewer_summary,
                "reviewer": payload.reviewer,
                "multiAgentWorktreeStrategy": payload.multi_agent_worktree_strategy,
                "diffPreviewBytes": payload.diff_preview_bytes,
            }),
        )
        .await
}

#[tauri::command]
async fn worktree_cleanup(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: WorktreeCleanupPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "worktree.cleanup".to_string(),
            json!({ "worktreeId": payload.worktree_id, "force": payload.force.unwrap_or(false) }),
        )
        .await
}

#[tauri::command]
async fn schedule_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskCreatePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "schedule.create".to_string(),
            json!({
                "name": payload.name,
                "prompt": payload.prompt,
                "schedule": payload.schedule,
                "enabled": payload.enabled,
                "status": payload.status,
            }),
        )
        .await
}

#[tauri::command]
async fn schedule_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "schedule.list".to_string(), json!({}))
        .await
}

#[tauri::command]
async fn schedule_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskUpdatePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "schedule.update".to_string(),
            json!({
                "taskId": payload.task_id,
                "name": payload.name,
                "prompt": payload.prompt,
                "schedule": payload.schedule,
                "enabled": payload.enabled,
                "status": payload.status,
            }),
        )
        .await
}

#[tauri::command]
async fn schedule_toggle(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskTogglePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "schedule.toggle".to_string(),
            json!({
                "taskId": payload.task_id,
                "enabled": payload.enabled,
            }),
        )
        .await
}

#[tauri::command]
async fn schedule_run_now(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ScheduledTaskIdPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "schedule.run_now".to_string(),
            json!({ "taskId": payload.task_id }),
        )
        .await
}

#[tauri::command]
async fn schedule_logs(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<ScheduledTaskLogsPayload>,
) -> Result<Value, String> {
    let params = match payload {
        Some(payload) => json!({
            "taskId": payload.task_id,
            "limit": payload.limit,
        }),
        None => json!({}),
    };
    state
        .call_async(app_handle, "schedule.logs".to_string(), params)
        .await
}

#[tauri::command]
async fn approval_submit(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ApprovalSubmitPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "approval.submit".to_string(),
            json!({
                "approvalId": payload.approval_id,
                "decision": payload.decision,
            }),
        )
        .await
}

#[tauri::command]
async fn config_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "config.get".to_string(), json!({}))
        .await
}

#[tauri::command]
async fn config_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "config.update".to_string(), payload)
        .await
}

#[tauri::command]
async fn provider_test(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "provider.test".to_string(), payload)
        .await
}

#[tauri::command]
async fn command_log_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: CommandLogGetPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "command_log.get".to_string(),
            json!({ "commandId": payload.command_id }),
        )
        .await
}

#[tauri::command]
async fn command_log_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<CommandLogListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state
        .call_async(
            app_handle,
            "command_log.list".to_string(),
            json!({
                "taskId": payload.task_id,
                "sessionId": payload.session_id,
                "status": payload.status,
                "limit": payload.limit,
            }),
        )
        .await
}

#[tauri::command]
async fn command_cancel(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: CommandCancelPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "command.cancel".to_string(),
            json!({ "commandId": payload.command_id }),
        )
        .await
}

#[tauri::command]
async fn diff_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: DiffGetPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "diff.get".to_string(),
            json!({ "patchId": payload.patch_id }),
        )
        .await
}

#[tauri::command]
async fn trace_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TraceListPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "trace.list".to_string(),
            json!({
                "taskId": payload.task_id,
                "limit": payload.limit,
            }),
        )
        .await
}

#[tauri::command]
async fn log_export(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<LogExportPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state
        .call_async(
            app_handle,
            "log.export".to_string(),
            json!({ "sessionId": payload.session_id }),
        )
        .await
}

#[tauri::command]
async fn errors_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<ErrorsListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state
        .call_async(
            app_handle,
            "errors.list".to_string(),
            json!({
                "sessionId": payload.session_id,
                "taskId": payload.task_id,
                "source": payload.source,
                "limit": payload.limit,
            }),
        )
        .await
}

#[tauri::command]
async fn metrics_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<MetricsListPayload>,
) -> Result<Value, String> {
    let payload = payload.unwrap_or_default();
    state
        .call_async(
            app_handle,
            "metrics.list".to_string(),
            json!({
                "sessionId": payload.session_id,
                "limit": payload.limit,
            }),
        )
        .await
}

#[tauri::command]
fn open_app_path(app_handle: AppHandle, kind: String) -> Result<Value, String> {
    let data_dir = resolve_data_dir(&app_handle)?;
    fs::create_dir_all(&data_dir)
        .map_err(|reason| format!("Failed to create data directory: {reason}"))?;

    let target = match kind.as_str() {
        "data" => data_dir,
        "logs" => {
            let logs_dir = data_dir.join("logs");
            fs::create_dir_all(&logs_dir)
                .map_err(|reason| format!("Failed to create logs directory: {reason}"))?;
            logs_dir
        }
        "skills" => {
            let skills_dir = resolve_skills_dir(&app_handle)?;
            fs::create_dir_all(&skills_dir)
                .map_err(|reason| format!("Failed to create skills directory: {reason}"))?;
            skills_dir
        }
        _ => return Err(format!("Unsupported app path kind: {kind}")),
    };

    open_path_in_file_manager(&target)?;
    Ok(json!({ "path": target.display().to_string() }))
}

#[tauri::command]
async fn skill_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "skill.list".to_string(),
            payload.unwrap_or_else(|| json!({})),
        )
        .await
}

#[tauri::command]
async fn skill_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "skill.create".to_string(), payload)
        .await
}

#[tauri::command]
async fn skill_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "skill.update".to_string(), payload)
        .await
}

#[tauri::command]
async fn skill_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "skill.delete".to_string(), payload)
        .await
}

#[tauri::command]
async fn mcp_server_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "mcp.server.list".to_string(),
            payload.unwrap_or_else(|| json!({})),
        )
        .await
}

#[tauri::command]
async fn mcp_server_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "mcp.server.create".to_string(), payload)
        .await
}

#[tauri::command]
async fn mcp_server_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "mcp.server.update".to_string(), payload)
        .await
}

#[tauri::command]
async fn mcp_server_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "mcp.server.delete".to_string(), payload)
        .await
}

#[tauri::command]
async fn mcp_tools_refresh(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "mcp.tools.refresh".to_string(),
            payload.unwrap_or_else(|| json!({})),
        )
        .await
}

#[tauri::command]
async fn agent_profile_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Option<Value>,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "agent.profile.list".to_string(),
            payload.unwrap_or_else(|| json!({})),
        )
        .await
}

#[tauri::command]
async fn agent_profile_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "agent.profile.create".to_string(), payload)
        .await
}

#[tauri::command]
async fn agent_profile_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "agent.profile.update".to_string(), payload)
        .await
}

#[tauri::command]
async fn agent_profile_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "agent.profile.delete".to_string(), payload)
        .await
}

#[tauri::command]
async fn agent_profile_validate(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "agent.profile.validate".to_string(), payload)
        .await
}

#[tauri::command]
async fn agent_profile_preview_tools(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "agent.profile.previewTools".to_string(),
            payload,
        )
        .await
}

#[tauri::command]
async fn hook_list(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "hook.list".to_string(), payload)
        .await
}

#[tauri::command]
async fn hook_create(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "hook.create".to_string(), payload)
        .await
}

#[tauri::command]
async fn hook_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "hook.update".to_string(), payload)
        .await
}

#[tauri::command]
async fn hook_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "hook.delete".to_string(), payload)
        .await
}

#[tauri::command]
async fn hook_get(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "hook.get".to_string(), payload)
        .await
}

#[tauri::command]
async fn hook_list_executions(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: Value,
) -> Result<Value, String> {
    state
        .call_async(app_handle, "hook.listExecutions".to_string(), payload)
        .await
}

#[tauri::command]
fn e2e_fixture() -> Result<Value, String> {
    let flow = env::var("YUANBAO_TAURI_E2E").unwrap_or_default();
    if flow == "ui-smoke"
        || flow == "mcp-live"
        || flow == "session-recovery-seed"
        || flow == "session-recovery-verify"
    {
        let repo_root = repo_root()?;
        let workspace_path = env::var("YUANBAO_TAURI_E2E_WORKSPACE")
            .unwrap_or_else(|_| repo_root.display().to_string());
        let prompt = env::var("YUANBAO_TAURI_E2E_PROMPT").unwrap_or_else(|_| {
            "Session recovery seed: persist this message across a desktop restart.".to_string()
        });
        let session_title = env::var("YUANBAO_TAURI_E2E_SESSION_TITLE")
            .unwrap_or_else(|_| "E2E Recovery Session".to_string());

        return Ok(json!({
            "enabled": true,
            "flow": flow,
            "workspacePath": workspace_path,
            "prompt": prompt,
            "sessionTitle": session_title,
        }));
    }

    if flow != "provider-flow" {
        return Ok(json!({ "enabled": false }));
    }

    let repo_root = repo_root()?;
    let api_key_env_var_name = env::var("YUANBAO_TAURI_E2E_API_KEY_ENV")
        .unwrap_or_else(|_| "LOCAL_AGENT_PROVIDER_API_KEY".to_string());
    if env::var(&api_key_env_var_name)
        .unwrap_or_default()
        .is_empty()
    {
        return Err(format!(
            "E2E provider API key env var is not set: {api_key_env_var_name}"
        ));
    }

    let workspace_path =
        env::var("YUANBAO_TAURI_E2E_WORKSPACE").unwrap_or_else(|_| repo_root.display().to_string());
    let prompt = env::var("YUANBAO_TAURI_E2E_PROMPT").unwrap_or_else(|_| {
        "Read-only check: confirm whether app/src/lib/runtimeClient.ts exists. Answer in one short sentence and do not modify files.".to_string()
    });

    Ok(json!({
        "enabled": true,
        "flow": flow,
        "workspacePath": workspace_path,
        "prompt": prompt,
        "autoApprove": env::var("YUANBAO_TAURI_E2E_AUTO_APPROVE")
            .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
            .unwrap_or(false),
        "provider": {
            "profileId": env::var("YUANBAO_TAURI_E2E_PROVIDER_ID").unwrap_or_else(|_| "e2e-provider".to_string()),
            "name": env::var("YUANBAO_TAURI_E2E_PROVIDER_NAME").unwrap_or_else(|_| "E2E Provider".to_string()),
            "baseUrl": env::var("YUANBAO_TAURI_E2E_BASE_URL").unwrap_or_else(|_| "https://api.ximeixg.cloud/v1".to_string()),
            "apiFormat": env::var("YUANBAO_TAURI_E2E_API_FORMAT").unwrap_or_else(|_| "openai-chat".to_string()),
            "model": env::var("YUANBAO_TAURI_E2E_MODEL").unwrap_or_else(|_| "MiniMax-M2.7-highspeed".to_string()),
            "apiKeyEnvVarName": api_key_env_var_name,
            "timeout": env::var("YUANBAO_TAURI_E2E_PROVIDER_TIMEOUT")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(120),
        }
    }))
}

#[tauri::command]
fn e2e_finish(app_handle: AppHandle, payload: Value) -> Result<(), String> {
    let flow = env::var("YUANBAO_TAURI_E2E").unwrap_or_default();
    if flow != "provider-flow"
        && flow != "mcp-live"
        && flow != "ui-smoke"
        && flow != "session-recovery-seed"
        && flow != "session-recovery-verify"
    {
        return Err("E2E result writing is disabled.".to_string());
    }

    let result_path = env::var("YUANBAO_TAURI_E2E_RESULT_PATH")
        .map_err(|_| "YUANBAO_TAURI_E2E_RESULT_PATH is required.".to_string())?;
    let result_path = PathBuf::from(result_path);
    if let Some(parent) = result_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|reason| format!("Failed to create E2E result directory: {reason}"))?;
    }

    let body = serde_json::to_string_pretty(&payload)
        .map_err(|reason| format!("Failed to serialize E2E result: {reason}"))?;
    fs::write(&result_path, body)
        .map_err(|reason| format!("Failed to write E2E result: {reason}"))?;

    if env::var("YUANBAO_TAURI_E2E_EXIT").unwrap_or_else(|_| "1".to_string()) != "0" {
        let exit_code = if payload.get("ok").and_then(Value::as_bool).unwrap_or(false) {
            0
        } else {
            1
        };
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(200));
            app_handle.exit(exit_code);
        });
    }

    Ok(())
}

pub fn build_app() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(RuntimeManager::default())
        .manage(TerminalManager::default())
        .invoke_handler(tauri::generate_handler![
            host_status,
            workspace_open,
            workspace_focus_update,
            workspace_memory_clear,
            workspace_memory_init,
            session_create,
            session_list,
            message_send,
            message_list,
            workspace_file_list,
            workspace_file_read,
            terminal_start,
            terminal_write,
            terminal_resize,
            terminal_stop,
            git_local_status,
            git_local_diff,
            git_local_init,
            git_local_checkout,
            git_local_commit,
            task_get,
            task_cancel,
            task_pause,
            task_resume,
            task_list,
            worktree_get,
            worktree_get_by_task,
            worktree_status,
            worktree_diff,
            worktree_merge,
            worktree_request_merge_approval,
            worktree_cleanup,
            schedule_create,
            schedule_list,
            schedule_update,
            schedule_toggle,
            schedule_run_now,
            schedule_logs,
            approval_submit,
            config_get,
            config_update,
            provider_test,
            command_log_get,
            command_log_list,
            command_cancel,
            diff_get,
            trace_list,
            log_export,
            errors_list,
            metrics_list,
            open_app_path,
            skill_list,
            skill_create,
            skill_update,
            skill_delete,
            mcp_server_list,
            mcp_server_create,
            mcp_server_update,
            mcp_server_delete,
            mcp_tools_refresh,
            agent_profile_list,
            agent_profile_create,
            agent_profile_update,
            agent_profile_delete,
            agent_profile_validate,
            agent_profile_preview_tools,
            hook_list,
            hook_create,
            hook_update,
            hook_delete,
            hook_get,
            hook_list_executions,
            e2e_fixture,
            e2e_finish
        ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_manager_clones_share_the_same_bridge() {
        let manager = RuntimeManager::default();
        let cloned = manager.clone();

        let _guard = manager.bridge.lock().expect("manager bridge lock");

        assert!(cloned.bridge.try_lock().is_err());
    }
}
