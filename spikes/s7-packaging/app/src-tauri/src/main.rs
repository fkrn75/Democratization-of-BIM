// SPDX-License-Identifier: MPL-2.0
// 릴리스에서 콘솔 창이 뜨지 않게 한다 (Windows)
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri_plugin_shell::ShellExt;

/// 동결 코어(dobim 사이드카)를 한 번 실행해 커맨드 목록 JSON 을 돌려준다.
/// 사이드카는 설치 폴더에 놓인 `dobim.exe` 이고, 옆의 `_internal` 폴더(resources)를 PyInstaller 가 찾는다.
#[tauri::command]
async fn core_list(app: tauri::AppHandle) -> Result<String, String> {
    let out = app
        .shell()
        .sidecar("dobim")
        .map_err(|e| e.to_string())?
        .args(["list", "--query", "wall"])
        .output()
        .await
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).to_string())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).to_string())
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![core_list])
        .run(tauri::generate_context!())
        .expect("Tauri 앱 시작 실패");
}
