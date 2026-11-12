(() => {
  const $ = (id) => document.getElementById(id);
  const input = $('videoInput');
  const maxFileBytes = Number(document.body.dataset.maxFileBytes || 0);
  const dropzone = $('dropzone');
  const browse = $('browseButton');
  const processButton = $('processButton');
  const progressPanel = $('progressPanel');
  const progressFill = $('progressFill');
  const progressPercent = $('progressPercent');
  const progressTitle = $('progressTitle');
  const progressMessage = $('progressMessage');
  const errorBox = $('errorBox');
  const resultPanel = $('resultPanel');
  let selectedFile = null;
  let inputUrl = null;
  let outputUrl = null;
  let currentJob = null;
  let pollTimer = null;

  $('year').textContent = new Date().getFullYear();
  const formatBytes = (bytes) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  const formatDuration = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const revoke = (url) => { if (url) URL.revokeObjectURL(url); };

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove('hidden');
  }
  function clearError() { errorBox.textContent = ''; errorBox.classList.add('hidden'); }
  function resetResult() {
    resultPanel.classList.add('hidden');
    revoke(outputUrl); outputUrl = null;
    $('outputPreview').removeAttribute('src');
    $('downloadButton').removeAttribute('href');
  }
  function selectFile(file) {
    clearError(); resetResult();
    if (!file) return;
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['mp4', 'mov', 'm4v'].includes(ext)) return showError('Format tidak didukung. Pilih MP4, MOV, atau M4V.');
    if (file.size <= 0) return showError('File yang dipilih kosong.');
    if (maxFileBytes && file.size > maxFileBytes) return showError('Ukuran video melebihi batas upload server.');
    selectedFile = file;
    $('fileName').textContent = file.name;
    $('fileInfo').textContent = formatBytes(file.size);
    $('selectedFile').classList.remove('hidden');
    $('previewGrid').classList.remove('hidden');
    revoke(inputUrl); inputUrl = URL.createObjectURL(file);
    $('inputPreview').src = inputUrl;
    $('sourceInfo').innerHTML = `<div class="info-row"><span>Nama file</span><b>${escapeHtml(file.name)}</b></div><div class="info-row"><span>Ukuran</span><b>${formatBytes(file.size)}</b></div><div class="info-row"><span>Format</span><b>${ext.toUpperCase()}</b></div><div class="info-row"><span>Status</span><b>Siap diproses</b></div>`;
    processButton.disabled = false;
    progressPanel.classList.add('hidden');
  }
  function escapeHtml(text) { return text.replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  dropzone.addEventListener('click', (event) => { if (event.target !== browse) input.click(); });
  dropzone.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); input.click(); } });
  browse.addEventListener('click', (event) => { event.stopPropagation(); input.click(); });
  input.addEventListener('change', () => selectFile(input.files[0]));
  $('removeFile').addEventListener('click', () => {
    selectedFile = null; input.value = ''; $('selectedFile').classList.add('hidden'); $('previewGrid').classList.add('hidden');
    revoke(inputUrl); inputUrl = null; $('inputPreview').removeAttribute('src'); processButton.disabled = true; clearError(); resetResult(); progressPanel.classList.add('hidden');
  });
  ['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, e => { e.preventDefault(); dropzone.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, e => { e.preventDefault(); dropzone.classList.remove('dragover'); }));
  dropzone.addEventListener('drop', e => selectFile(e.dataTransfer.files[0]));

  async function pollJob(jobId) {
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Tidak dapat memeriksa status pemrosesan.');
      if (data.status === 'queued' || data.status === 'processing') {
        progressTitle.textContent = data.status === 'queued' ? 'Menunggu antrean…' : 'Mengoptimalkan video…';
        progressMessage.textContent = data.message || 'Server sedang memproses video.';
        progressFill.style.width = '100%';
        progressPercent.textContent = '…';
        pollTimer = setTimeout(() => pollJob(jobId), 1600);
        return;
      }
      if (data.status === 'error') throw new Error(data.message || 'Pemrosesan gagal.');
      if (data.status === 'done') {
        progressTitle.textContent = 'Pemrosesan selesai'; progressMessage.textContent = 'Video berhasil dioptimalkan.';
        progressFill.style.width = '100%'; progressPercent.textContent = '100%';
        renderResult(data, jobId);
      }
    } catch (error) {
      showError(error.message || 'Terjadi kesalahan. Silakan coba lagi.');
      progressPanel.classList.add('hidden'); processButton.disabled = false;
    }
  }
  function renderResult(data, jobId) {
    const details = data.details;
    const before = details.before, after = details.after;
    outputUrl = `/download/${jobId}`;
    $('outputPreview').src = `/preview/${jobId}`;
    $('downloadButton').href = outputUrl;
    $('comparison').innerHTML = `
      <div class="metric"><span>RESOLUSI HASIL</span><strong>${after.width} × ${after.height}</strong></div>
      <div class="metric"><span>FRAME RATE</span><strong>${after.fps ?? 'Sesuai sumber'} FPS</strong></div>
      <div class="metric"><span>UKURAN FILE</span><strong>${formatBytes(after.size)}</strong></div>
      <div class="metric"><span>DURASI</span><strong>${formatDuration(after.duration)}</strong></div>`;
    $('sourceInfo').innerHTML = `<div class="info-row"><span>Resolusi awal</span><b>${before.width} × ${before.height}</b></div><div class="info-row"><span>FPS awal</span><b>${before.fps ?? 'Tidak diketahui'}</b></div><div class="info-row"><span>Codec hasil</span><b>H.264</b></div><div class="info-row"><span>Audio</span><b>${after.audio ? 'AAC' : 'Tidak ada'}</b></div>`;
    resultPanel.classList.remove('hidden');
    processButton.disabled = false;
    resultPanel.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  processButton.addEventListener('click', () => {
    if (!selectedFile || processButton.disabled) return;
    clearError(); resetResult();
    processButton.disabled = true;
    progressPanel.classList.remove('hidden');
    progressTitle.textContent = 'Mengunggah video…'; progressMessage.textContent = 'Mengirim file ke server dengan aman.';
    progressFill.style.width = '0%'; progressPercent.textContent = '0%';
    const form = new FormData(); form.append('video', selectedFile, selectedFile.name);
    const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/optimize'); xhr.responseType = 'json';
    xhr.upload.addEventListener('progress', event => {
      if (event.lengthComputable) {
        const pct = Math.round(event.loaded / event.total * 100);
        progressFill.style.width = `${pct}%`; progressPercent.textContent = `${pct}%`;
      }
    });
    xhr.upload.addEventListener('load', () => {
      progressTitle.textContent = 'Mengoptimalkan video…'; progressMessage.textContent = 'Upload selesai. FFmpeg sedang memproses video di server.';
      progressPercent.textContent = '…'; progressFill.style.width = '100%';
    });
    xhr.addEventListener('load', () => {
      const data = xhr.response || {};
      if (xhr.status < 200 || xhr.status >= 300) {
        showError(data.error || 'Upload gagal. Silakan coba lagi.'); progressPanel.classList.add('hidden'); processButton.disabled = false; return;
      }
      currentJob = data.job_id; pollJob(currentJob);
    });
    xhr.addEventListener('error', () => { showError('Koneksi terputus saat upload. Periksa internet lalu coba lagi.'); progressPanel.classList.add('hidden'); processButton.disabled = false; });
    xhr.addEventListener('timeout', () => { showError('Upload melewati batas waktu. Coba koneksi yang lebih stabil.'); progressPanel.classList.add('hidden'); processButton.disabled = false; });
    xhr.timeout = 10 * 60 * 1000;
    xhr.send(form);
  });
  window.addEventListener('beforeunload', () => { clearTimeout(pollTimer); revoke(inputUrl); });
})();
