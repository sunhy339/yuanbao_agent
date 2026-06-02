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

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ComputerUseProbe {
    status: &'static str,
    checked_at: u128,
    platform: String,
    desktop_bridge: bool,
    runtime_running: bool,
    capabilities: Vec<ComputerUseProbeCapability>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ComputerUseProbeCapability {
    id: &'static str,
    label: &'static str,
    state: &'static str,
    detail: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionCreatePayload {
    workspace_id: String,
    title: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionUpdatePayload {
    session_id: String,
    title: Option<String>,
    status: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionIdPayload {
    session_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionCompactPayload {
    session_id: String,
    max_tokens: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionMessageTargetPayload {
    session_id: String,
    message_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SessionBranchPayload {
    session_id: String,
    message_id: String,
    title: Option<String>,
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
    file_references: Option<Vec<String>>,
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
struct TaskRevertChangesPayload {
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
struct ApprovalAllowAlwaysPayload {
    approval_id: String,
    scope: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PermissionRuleClearPayload {
    capability: String,
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
struct WorkspaceFileSearchPayload {
    workspace_root: String,
    query: Option<String>,
    max_entries: Option<usize>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileSearchCandidate {
    entry: WorkspaceFileEntryView,
    score: i64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkspaceFileReadPayload {
    workspace_root: String,
    path: String,
    max_bytes: Option<usize>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct OpenPathPayload {
    path: String,
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
        .env(
            "TERM",
            if cfg!(target_os = "windows") {
                "dumb"
            } else {
                "xterm-256color"
            },
        );

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

fn git_stdout_optional(cwd: &Path, args: &[&str]) -> Option<String> {
    git_stdout(cwd, args)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
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

fn normalize_remote_branch(ref_name: &str) -> Option<(String, String)> {
    let trimmed = ref_name.trim();
    if trimmed.is_empty() || trimmed.ends_with("/HEAD") {
        return None;
    }
    let slash = trimmed.find('/')?;
    if slash == 0 {
        return None;
    }
    let remote = &trimmed[..slash];
    let name = &trimmed[slash + 1..];
    if name.is_empty() {
        return None;
    }
    let display_name = if remote == "origin" {
        name.to_string()
    } else {
        trimmed.to_string()
    };
    Some((display_name, trimmed.to_string()))
}

fn parse_worktree_list(stdout: &str, cwd: &Path) -> Vec<Value> {
    let mut worktrees = Vec::new();
    let mut current_path: Option<String> = None;
    let mut current_branch: Option<String> = None;

    for line in stdout.lines() {
        if let Some(path_text) = line.strip_prefix("worktree ") {
            if let Some(path) = current_path.take() {
                let is_current = PathBuf::from(&path)
                    .canonicalize()
                    .ok()
                    .map(|path| path == cwd)
                    .unwrap_or(false);
                worktrees.push(json!({
                    "path": path,
                    "branch": current_branch.take(),
                    "current": is_current,
                }));
            }
            current_path = Some(path_text.trim().to_string());
            current_branch = None;
            continue;
        }

        if let Some(ref_name) = line.strip_prefix("branch ") {
            current_branch = Some(
                ref_name
                    .trim()
                    .strip_prefix("refs/heads/")
                    .unwrap_or(ref_name.trim())
                    .to_string(),
            );
        }
    }

    if let Some(path) = current_path.take() {
        let is_current = PathBuf::from(&path)
            .canonicalize()
            .ok()
            .map(|path| path == cwd)
            .unwrap_or(false);
        worktrees.push(json!({
            "path": path,
            "branch": current_branch.take(),
            "current": is_current,
        }));
    }

    worktrees
}

fn default_branch(cwd: &Path) -> Option<String> {
    if let Some(origin_head) = git_stdout_optional(cwd, &["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"]) {
        if let Some(value) = origin_head.strip_prefix("origin/") {
            return Some(value.to_string());
        }
        return Some(origin_head);
    }
    git_stdout_optional(cwd, &["branch", "--show-current"])
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
    let worktree_raw = git_stdout(&repo_root, &["worktree", "list", "--porcelain"]).unwrap_or_default();
    let worktrees = parse_worktree_list(&worktree_raw, cwd);
    let checked_out_by_branch: HashMap<String, String> = worktrees
        .iter()
        .filter_map(|worktree| {
            let branch = worktree.get("branch").and_then(Value::as_str)?;
            let path = worktree.get("path").and_then(Value::as_str)?;
            Some((branch.to_string(), path.to_string()))
        })
        .collect();

    let branch_raw = git_stdout(cwd, &["branch", "--format=%(HEAD)%09%(refname:short)", "--list"])?;
    let mut branches: Vec<Value> = branch_raw
        .lines()
        .filter_map(|line| {
            let (head, name) = line.split_once('\t')?;
            let trimmed = name.trim();
            if trimmed.is_empty() {
                return None;
            }
            let worktree_path = checked_out_by_branch.get(trimmed).cloned();
            Some(json!({
                "name": trimmed,
                "current": head.trim() == "*",
                "local": true,
                "remote": false,
                "remoteRef": null,
                "checkedOut": worktree_path.is_some(),
                "worktreePath": worktree_path,
            }))
        })
        .collect();
    let existing_branch_names: std::collections::HashSet<String> = branches
        .iter()
        .filter_map(|branch| branch.get("name").and_then(Value::as_str).map(str::to_string))
        .collect();
    if let Ok(remote_raw) = git_stdout(cwd, &["for-each-ref", "--format=%(refname:short)", "refs/remotes"]) {
        for line in remote_raw.lines() {
            if let Some((name, remote_ref)) = normalize_remote_branch(line) {
                if existing_branch_names.contains(&name) {
                    for branch_value in branches.iter_mut() {
                        if branch_value.get("name").and_then(Value::as_str) == Some(name.as_str()) {
                            if let Some(object) = branch_value.as_object_mut() {
                                object.insert("remote".to_string(), json!(true));
                                object.insert("remoteRef".to_string(), json!(remote_ref));
                            }
                        }
                    }
                } else {
                    branches.push(json!({
                        "name": name,
                        "current": false,
                        "local": false,
                        "remote": true,
                        "remoteRef": remote_ref,
                        "checkedOut": false,
                        "worktreePath": null,
                    }));
                }
            }
        }
    }
    branches.sort_by(|left, right| {
        let left_current = left.get("current").and_then(Value::as_bool).unwrap_or(false);
        let right_current = right.get("current").and_then(Value::as_bool).unwrap_or(false);
        if left_current != right_current {
            return right_current.cmp(&left_current);
        }
        let left_local = left.get("local").and_then(Value::as_bool).unwrap_or(false);
        let right_local = right.get("local").and_then(Value::as_bool).unwrap_or(false);
        if left_local != right_local {
            return right_local.cmp(&left_local);
        }
        let left_name = left.get("name").and_then(Value::as_str).unwrap_or("");
        let right_name = right.get("name").and_then(Value::as_str).unwrap_or("");
        left_name.cmp(right_name)
    });
    let dirty_files = files.len();

    Ok(json!({
        "cwd": cwd.display().to_string(),
        "repoRoot": repo_root.display().to_string(),
        "repoName": repo_root.file_name().and_then(|name| name.to_str()).unwrap_or_default(),
        "branch": branch,
        "defaultBranch": default_branch(cwd),
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "dirtyFiles": dirty_files,
        "files": files,
        "branches": branches,
        "worktrees": worktrees,
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

fn should_skip_workspace_search_dir(name: &str) -> bool {
    matches!(
        name,
        ".git"
            | ".hg"
            | ".svn"
            | ".idea"
            | ".vscode"
            | "node_modules"
            | "target"
            | "dist"
            | "build"
            | ".next"
            | ".nuxt"
            | ".turbo"
            | ".venv"
            | "venv"
            | "__pycache__"
    )
}

fn workspace_search_score(path: &str, name: &str, query: &str) -> Option<i64> {
    if query.is_empty() {
        return Some(0);
    }
    let haystack = path.to_lowercase();
    let name_haystack = name.to_lowercase();
    let query_lower = query.to_lowercase();
    if name_haystack == query_lower {
        return Some(400);
    }
    if haystack == query_lower {
        return Some(360);
    }
    if name_haystack.starts_with(&query_lower) {
        return Some(320 - name_haystack.len() as i64);
    }
    if haystack.starts_with(&query_lower) {
        return Some(280 - haystack.len() as i64);
    }
    if name_haystack.contains(&query_lower) {
        return Some(220 - name_haystack.len() as i64);
    }
    if haystack.contains(&query_lower) {
        return Some(180 - haystack.len() as i64);
    }

    let mut last_index = 0usize;
    let mut gaps = 0i64;
    for needle in query_lower.chars() {
        let remainder = &haystack[last_index..];
        let Some(index) = remainder.find(needle) else {
            return None;
        };
        gaps += index as i64;
        last_index += index + needle.len_utf8();
    }
    Some(120 - gaps - haystack.len() as i64)
}

fn append_path_env(name: &str, first_path: &Path) -> Result<std::ffi::OsString, String> {
    let mut paths = vec![first_path.to_path_buf()];
    if let Some(existing) = env::var_os(name) {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).map_err(|reason| format!("Failed to prepare {name}: {reason}"))
}

fn run_python_probe(script: &str) -> Result<Value, String> {
    let runtime_src = resolve_runtime_src()?;
    let python_executable = env::var("LOCAL_AGENT_PYTHON").unwrap_or_else(|_| "python".to_string());
    let output = Command::new(python_executable)
        .arg("-c")
        .arg(script)
        .env("PYTHONPATH", append_path_env("PYTHONPATH", &runtime_src)?)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .map_err(|reason| format!("Failed to run Python capability probe: {reason}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("Python capability probe exited with {}", output.status)
        } else {
            stderr
        });
    }

    serde_json::from_slice(&output.stdout)
        .map_err(|reason| format!("Failed to parse Python capability probe: {reason}"))
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
fn computer_use_probe(state: State<'_, RuntimeManager>) -> Result<ComputerUseProbe, String> {
    let checked_at = now_ms();
    let platform = env::consts::OS.to_string();
    let runtime_running = state.runtime_running();
    let probe = run_python_probe(
        r#"
import importlib.util, json, os, platform

def has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False

print(json.dumps({
    "pillowImageGrab": has_module("PIL.ImageGrab"),
    "pyautogui": has_module("pyautogui"),
    "playwright": has_module("playwright.sync_api"),
    "playwrightEnabled": os.environ.get("LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT", "").strip().lower() in {"1", "true", "yes", "on"},
    "windows": platform.system().lower() == "windows",
    "display": os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("SESSIONNAME") or "",
}, ensure_ascii=False))
"#,
    );
    let (image_grab, pyautogui, playwright, playwright_enabled, windows, display, probe_error) =
        match probe {
            Ok(value) => (
                value
                    .get("pillowImageGrab")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                value
                    .get("pyautogui")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                value
                    .get("playwright")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                value
                    .get("playwrightEnabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                value
                    .get("windows")
                    .and_then(Value::as_bool)
                    .unwrap_or(cfg!(target_os = "windows")),
                value
                    .get("display")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                None,
            ),
            Err(reason) => (
                false,
                false,
                false,
                false,
                cfg!(target_os = "windows"),
                String::new(),
                Some(reason),
            ),
        };
    let desktop_fallback = windows;
    let desktop_state = if pyautogui {
        "ready"
    } else if desktop_fallback {
        "partial"
    } else {
        "guarded"
    };
    let status = if image_grab && (pyautogui || desktop_fallback) {
        "ready"
    } else {
        "degraded"
    };
    let screenshot_detail = if image_grab {
        "Pillow ImageGrab 可导入，截图 action 会在执行时尝试抓屏。".to_string()
    } else if let Some(error) = &probe_error {
        format!("Python 探测暂不可用；截图执行时仍会返回明确失败原因。{error}")
    } else {
        "Pillow ImageGrab 未探测到；截图执行时会返回明确失败原因。".to_string()
    };
    let desktop_detail = if pyautogui {
        format!(
            "pyautogui 可导入；坐标点击、键入、按键和滚动可走桌面 executor。display={}",
            if display.is_empty() {
                "unknown"
            } else {
                display.as_str()
            }
        )
    } else if desktop_fallback {
        "pyautogui 未探测到；Windows ctypes fallback 可处理坐标点击和滚动，键入/按键仍建议安装 pyautogui 或注入 executor。".to_string()
    } else if let Some(error) = &probe_error {
        format!("Python 探测暂不可用；桌面动作会在运行时尝试可用 executor。{error}")
    } else {
        "未探测到 pyautogui 或平台 fallback；需要安装 pyautogui 或注入自定义 executor。".to_string()
    };
    let browser_detail = if playwright && playwright_enabled {
        "Playwright sync API 可导入，且 LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT 已开启；browser inspect/screenshot 和 selector click/type/scroll 可通过宿主浏览器 session executor 尝试执行。".to_string()
    } else if playwright {
        "Playwright sync API 可导入；设置 LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT=1 后可启用宿主浏览器 session executor。".to_string()
    } else {
        "Playwright page-like executor 协议已就绪；当前未探测到 Playwright sync API，宿主仍可注入真实浏览器会话对象。".to_string()
    };

    Ok(ComputerUseProbe {
        status,
        checked_at,
        platform,
        desktop_bridge: true,
        runtime_running,
        capabilities: vec![
            ComputerUseProbeCapability {
                id: "permission-audit",
                label: "权限审计",
                state: "ready",
                detail: "computer_use 会先走运行时审批，并把申请与审批结果写入专用事件。"
                    .to_string(),
            },
            ComputerUseProbeCapability {
                id: "screen-observation",
                label: "屏幕观察",
                state: if image_grab { "ready" } else { "guarded" },
                detail: screenshot_detail,
            },
            ComputerUseProbeCapability {
                id: "desktop-actions",
                label: "桌面动作",
                state: desktop_state,
                detail: desktop_detail,
            },
            ComputerUseProbeCapability {
                id: "browser-dom",
                label: "浏览器 DOM 控制",
                state: if playwright && playwright_enabled {
                    "ready"
                } else if playwright {
                    "guarded"
                } else {
                    "partial"
                },
                detail: browser_detail,
            },
            ComputerUseProbeCapability {
                id: "clipboard",
                label: "剪贴板",
                state: "guarded",
                detail: "系统级剪贴板读写仍需显式权限与宿主 API；前端会单独探测浏览器剪贴板写入。"
                    .to_string(),
            },
            ComputerUseProbeCapability {
                id: "system-key-combos",
                label: "系统快捷键",
                state: "guarded",
                detail: "单键 press 已接入；系统级组合键继续保留在显式确认和后续宿主能力扩展下。"
                    .to_string(),
            },
        ],
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
async fn session_update(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionUpdatePayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "session.update".to_string(),
            json!({
                "sessionId": payload.session_id,
                "title": payload.title,
                "status": payload.status,
            }),
        )
        .await
}

#[tauri::command]
async fn session_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionIdPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "session.delete".to_string(),
            json!({ "sessionId": payload.session_id }),
        )
        .await
}

#[tauri::command]
async fn session_compact(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionCompactPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "session.compact".to_string(),
            json!({ "sessionId": payload.session_id, "maxTokens": payload.max_tokens }),
        )
        .await
}

#[tauri::command]
async fn session_branch(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionBranchPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "session.branch".to_string(),
            json!({
                "sessionId": payload.session_id,
                "messageId": payload.message_id,
                "title": payload.title,
            }),
        )
        .await
}

#[tauri::command]
async fn session_truncate(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionMessageTargetPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "session.truncate".to_string(),
            json!({ "sessionId": payload.session_id, "messageId": payload.message_id }),
        )
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
                "fileReferences": payload.file_references.unwrap_or_default(),
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
async fn message_delete(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: SessionMessageTargetPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "message.delete".to_string(),
            json!({ "sessionId": payload.session_id, "messageId": payload.message_id }),
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
fn workspace_file_search(payload: WorkspaceFileSearchPayload) -> Result<Value, String> {
    let (root, _) = resolve_workspace_path(&payload.workspace_root, None)?;
    let query = payload.query.unwrap_or_default().trim().to_string();
    let limit = payload.max_entries.unwrap_or(24).clamp(1, 80);
    let scan_limit = 20_000usize;
    let mut scanned = 0usize;
    let mut truncated = false;
    let mut stack = vec![root.clone()];
    let mut candidates: Vec<WorkspaceFileSearchCandidate> = Vec::new();

    while let Some(directory) = stack.pop() {
        let read_dir = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry_result in read_dir {
            if scanned >= scan_limit {
                truncated = true;
                break;
            }
            let Ok(entry) = entry_result else {
                continue;
            };
            let name = entry.file_name().to_string_lossy().to_string();
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() {
                if !should_skip_workspace_search_dir(&name) {
                    stack.push(entry.path());
                }
                continue;
            }
            if !file_type.is_file() {
                continue;
            }

            scanned += 1;
            let path = relative_path_string(&root, &entry.path());
            let Some(score) = workspace_search_score(&path, &name, &query) else {
                continue;
            };
            let metadata = entry
                .metadata()
                .map_err(|reason| format!("Failed to read file metadata: {reason}"))?;
            candidates.push(WorkspaceFileSearchCandidate {
                score,
                entry: WorkspaceFileEntryView {
                    name,
                    path,
                    kind: "file".to_string(),
                    size: Some(metadata.len()),
                    modified_at: file_modified_at_ms(&metadata),
                },
            });
        }
        if scanned >= scan_limit {
            break;
        }
    }

    candidates.sort_by(|left, right| {
        right.score.cmp(&left.score).then_with(|| {
            left.entry
                .path
                .to_lowercase()
                .cmp(&right.entry.path.to_lowercase())
        })
    });
    if candidates.len() > limit {
        truncated = true;
        candidates.truncate(limit);
    }

    Ok(json!({
        "rootPath": root.display().to_string(),
        "query": query,
        "entries": candidates.into_iter().map(|candidate| candidate.entry).collect::<Vec<_>>(),
        "truncated": truncated,
        "scanned": scanned,
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
async fn task_revert_changes(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: TaskRevertChangesPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "task.revertChanges".to_string(),
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
async fn approval_allow_always(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: ApprovalAllowAlwaysPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "approval.allowAlways".to_string(),
            json!({
                "approvalId": payload.approval_id,
                "scope": payload.scope,
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
async fn permission_rule_clear(
    app_handle: AppHandle,
    state: State<'_, RuntimeManager>,
    payload: PermissionRuleClearPayload,
) -> Result<Value, String> {
    state
        .call_async(
            app_handle,
            "permission.rule.clear".to_string(),
            json!({
                "capability": payload.capability,
            }),
        )
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
fn open_path(payload: OpenPathPayload) -> Result<Value, String> {
    let target = PathBuf::from(payload.path.trim())
        .canonicalize()
        .map_err(|reason| format!("Failed to resolve path: {reason}"))?;
    if !target.exists() {
        return Err("Path does not exist".to_string());
    }
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
            computer_use_probe,
            workspace_open,
            workspace_focus_update,
            workspace_memory_clear,
            workspace_memory_init,
            session_create,
            session_list,
            session_update,
            session_delete,
            session_compact,
            session_branch,
            session_truncate,
            message_send,
            message_list,
            message_delete,
            workspace_file_list,
            workspace_file_search,
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
            task_revert_changes,
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
            approval_allow_always,
            config_get,
            config_update,
            permission_rule_clear,
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
            open_path,
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
