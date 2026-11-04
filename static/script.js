const form = document.getElementById("clipForm");
const statusBox = document.getElementById("status");
const submitBtn = document.getElementById("submitBtn");

function showStatus(message, error = false) {
    statusBox.className = "status show";
    statusBox.style.border = error
        ? "1px solid #b94b4b"
        : "1px solid #39465a";
    statusBox.textContent = message;
}

document.getElementById("pasteBtn").addEventListener("click", async () => {
    try {
        document.getElementById("url").value =
            await navigator.clipboard.readText();
    } catch {
        showStatus("Silakan tempel URL secara manual.", true);
    }
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    submitBtn.disabled = true;
    submitBtn.textContent = "Memproses...";

    try {
        const payload = {
            url: document.getElementById("url").value.trim(),
            start: document.getElementById("start").value.trim(),
            end: document.getElementById("end").value.trim(),
            quality: document.getElementById("quality").value
        };

        const response = await fetch("/api/jobs", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Permintaan gagal.");
        }

        showStatus("Permintaan diterima. Sedang memproses video...");

        await pollJob(data.job_id);

    } catch (error) {
        showStatus("Gagal: " + error.message, true);
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Buat Potongan Video →";
    }
});

async function pollJob(id) {
    for (let i = 0; i < 240; i++) {
        await new Promise(resolve => setTimeout(resolve, 3000));

        const response = await fetch(`/api/jobs/${id}`);
        const data = await response.json();

        if (data.status === "completed") {
            statusBox.className = "status show";
            statusBox.innerHTML =
                `Video berhasil dibuat. <a href="${data.download_url}">Unduh MP4</a>`;
            return;
        }

        if (data.status === "failed") {
            throw new Error(data.error || "Proses video gagal.");
        }

        showStatus("Sedang memproses video... Status: " + data.status);
    }

    throw new Error("Proses terlalu lama. Silakan coba lagi.");
}
