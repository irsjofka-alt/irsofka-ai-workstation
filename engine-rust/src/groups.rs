//! Percakapan grup: beberapa worker menjawab berurutan dengan satu SOP.
//!
//! Tiap anggota menerima SOP, riwayat percakapan, dan pesan operator. Hasil satu
//! anggota menjadi bagian riwayat anggota berikutnya, sehingga mereka benar-benar
//! merespons satu sama lain — bukan menjawab paralel tanpa konteks.

use crate::api::AppState;
use crate::workflows::run_prompt_with_run;

pub(crate) async fn dispatch(state: AppState, group_id: String, content: String) {
    let Some((name, sop, members)) = state.store.get_group(&group_id).await else {
        return;
    };
    if members.is_empty() {
        state
            .store
            .save_group_message(
                &group_id,
                None,
                "system",
                "Grup ini belum punya anggota.",
                None,
            )
            .await;
        return;
    }

    // `@nama` / `@slug` membatasi giliran ke anggota yang disebut. Tanpa mention,
    // perilaku lama (semua anggota, berurutan sesuai SOP) tetap berlaku.
    let members = {
        let items = state.wakers.read().await;
        let mut targeted: Vec<String> = Vec::new();
        for token in content.split_whitespace().filter(|t| t.starts_with('@')) {
            let needle = token
                .trim_start_matches('@')
                .trim_matches(|c: char| !c.is_alphanumeric() && c != '-' && c != '_')
                .to_lowercase();
            if needle.is_empty() {
                continue;
            }
            if let Some(waker) = items.iter().find(|w| {
                w.slug.eq_ignore_ascii_case(&needle) || w.name.eq_ignore_ascii_case(&needle)
            }) {
                if !targeted.contains(&waker.id) {
                    targeted.push(waker.id.clone());
                }
            }
        }
        if targeted.is_empty() {
            members
        } else {
            members
                .into_iter()
                .filter(|(id, _, _)| targeted.contains(id))
                .collect()
        }
    };
    if members.is_empty() {
        state
            .store
            .save_group_message(
                &group_id,
                None,
                "system",
                "Tidak ada anggota grup yang cocok dengan mention itu.",
                None,
            )
            .await;
        return;
    }

    let history = state.store.group_messages(&group_id).await;
    let mut transcript = render_transcript(&history);

    for (waker_id, waker_name, _role) in members {
        let waker = {
            let items = state.wakers.read().await;
            items.iter().find(|w| w.id == waker_id).cloned()
        };
        let Some(waker) = waker else {
            state
                .store
                .save_group_message(
                    &group_id,
                    None,
                    "system",
                    &format!("{waker_name} sudah tidak ada; dilewati."),
                    None,
                )
                .await;
            continue;
        };
        if waker.status != "active" {
            state
                .store
                .save_group_message(
                    &group_id,
                    Some(&waker_id),
                    "system",
                    &format!("{waker_name} sedang dijeda; dilewati."),
                    None,
                )
                .await;
            continue;
        }

        let prompt = format!(
            "SOP grup \"{name}\":\n{sop}\n\nRiwayat percakapan:\n{transcript}\n\nPesan operator:\n{content}\n\nJawab sebagai {waker_name} sesuai SOP. Ringkas dan jangan mengulang jawaban anggota lain."
        );

        match run_prompt_with_run(&state, &waker, &prompt).await {
            Ok((run_id, output)) => {
                state
                    .store
                    .save_group_message(
                        &group_id,
                        Some(&waker_id),
                        "waker",
                        &output,
                        Some(&run_id),
                    )
                    .await;
                transcript.push_str(&format!("\n{waker_name}: {output}"));
            }
            Err(error) => {
                state
                    .store
                    .save_group_message(
                        &group_id,
                        Some(&waker_id),
                        "system",
                        &format!("{waker_name} gagal: {error}"),
                        None,
                    )
                    .await;
            }
        }
    }
}

fn render_transcript(history: &[(String, String, String)]) -> String {
    history
        .iter()
        .rev()
        .take(10)
        .rev()
        .map(|(who, role, text)| {
            let label = if role == "user" { "Operator" } else { who };
            format!("{label}: {}", text.chars().take(500).collect::<String>())
        })
        .collect::<Vec<_>>()
        .join("\n")
}
