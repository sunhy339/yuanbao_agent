fn main() {
    local_agent_shell::build_app()
        .run(tauri::generate_context!())
        .expect("failed to run local agent shell");
}
