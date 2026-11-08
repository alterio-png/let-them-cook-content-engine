"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const FORMATS = {
    vertical: {label: "9:16", dimensions: "270 × 480", description: "Vertical · amostra sintética", bytes: 60531},
    square: {label: "1:1", dimensions: "320 × 320", description: "Quadrado · amostra sintética", bytes: 65956},
    feed: {label: "4:5", dimensions: "320 × 400", description: "Feed · amostra sintética", bytes: 65998},
  };
  const WALKTHROUGH = [
    {
      title: "Uma origem sintética aprovada",
      copy: "No laboratório local, uma conta autenticada seleciona o vídeo sintético e a legenda SRT aprovados. Aqui você explora apenas a amostra já produzida.",
    },
    {
      title: "Upload privado e ingestão validada",
      copy: "A API local reserva o upload no PostgreSQL. O serviço interno verifica bytes, proveniência, áudio e SRT antes de criar o asset. Este passo é uma explicação; nenhum arquivo é enviado neste preview.",
    },
    {
      title: "Um job, três formatos",
      copy: "No pipeline local, job e outbox são criados juntos. O worker FFmpeg processa os formatos com limites e controle de execução. Nenhum render novo é iniciado por este percurso demonstrativo.",
    },
    {
      title: "Resultados reais, integridade verificável",
      copy: "Os três MP4 abaixo foram gerados pelo pipeline local do PR #16. Você pode reproduzir, conferir o SHA-256 e baixar esses arquivos públicos de teste. O laboratório usa downloads privados autenticados.",
    },
  ];
  let selected = "vertical";
  let sequence = 0;
  let activeController = null;
  let verifiedURL = null;
  let walkthroughStep = 0;

  function announce(message) {
    if ($("live-status")) $("live-status").textContent = message;
  }

  function filePath(preset) { return `media/${preset}.mp4`; }

  function revokeVerified() {
    if (verifiedURL) URL.revokeObjectURL(verifiedURL);
    verifiedURL = null;
  }

  function setRawLink() {
    const link = $("download-link");
    link.href = filePath(selected);
    link.removeAttribute("download");
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Abrir MP4 de teste";
  }

  function cancelVerification() {
    sequence += 1;
    if (activeController) activeController.abort();
    activeController = null;
    revokeVerified();
    $("verify-button").disabled = false;
    $("verify-button").textContent = "Verificar integridade";
  }

  function selectFormat(preset) {
    if (!Object.prototype.hasOwnProperty.call(FORMATS, preset) || preset === selected) return;
    cancelVerification();
    selected = preset;
    const format = FORMATS[preset];
    const video = $("output-video");
    video.pause();
    video.poster = `media/${preset}.jpg`;
    $("video-source").src = filePath(preset);
    video.load();
    $("format-label").textContent = format.label;
    $("format-dimensions").textContent = format.dimensions;
    $("format-description").textContent = format.description;
    $("output-size").textContent = `${(format.bytes / 1024).toFixed(1).replace(".", ",")} KiB`;
    $("integrity-status").textContent = "SHA-256 ainda não conferido neste navegador.";
    $("integrity-status").dataset.state = "idle";
    document.querySelectorAll("[data-format]").forEach((button) => {
      const active = button.dataset.format === preset;
      button.setAttribute("aria-pressed", String(active));
      button.dataset.selected = String(active);
    });
    setRawLink();
    announce(`Formato ${format.label} selecionado. Reprodução e verificação disponíveis.`);
  }

  async function verify() {
    cancelVerification();
    setRawLink();
    const currentSequence = sequence;
    const preset = selected;
    const controller = new AbortController();
    activeController = controller;
    const timer = setTimeout(() => controller.abort(), 8000);
    const button = $("verify-button");
    const status = $("integrity-status");
    button.disabled = true;
    button.textContent = "Verificando…";
    status.dataset.state = "pending";
    status.textContent = "Conferindo os bytes do MP4 com o manifesto do PR #16…";

    try {
      if (!globalThis.crypto?.subtle) throw new Error("unsupported");
      const manifestResponse = await fetch("media/manifest.json", {signal: controller.signal, cache: "no-store"});
      if (!manifestResponse.ok) throw new Error("manifest");
      const manifest = await manifestResponse.json();
      const output = Array.isArray(manifest.outputs)
        ? manifest.outputs.find((item) => item.preset === preset)
        : null;
      if (!output || !/^[a-f0-9]{64}$/.test(output.sha256) || output.size_bytes !== FORMATS[preset].bytes) {
        throw new Error("manifest");
      }
      const response = await fetch(filePath(preset), {signal: controller.signal, cache: "no-store"});
      if (!response.ok) throw new Error("media");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== output.size_bytes) throw new Error("integrity");
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const actual = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
      if (actual !== output.sha256) throw new Error("integrity");
      if (sequence !== currentSequence || controller.signal.aborted) return;
      verifiedURL = URL.createObjectURL(new Blob([bytes], {type: "video/mp4"}));
      const link = $("download-link");
      link.href = verifiedURL;
      link.download = `letthemcook-amostra-${preset}.mp4`;
      link.removeAttribute("target");
      link.textContent = "Baixar MP4 verificado";
      status.dataset.state = "verified";
      status.textContent = "SHA-256 e tamanho conferidos neste navegador. Arquivo de teste íntegro.";
      button.textContent = "Verificar novamente";
      announce(`Integridade do formato ${FORMATS[preset].label} confirmada. Download verificado disponível.`);
    } catch (error) {
      if (sequence !== currentSequence) return;
      revokeVerified();
      setRawLink();
      status.dataset.state = "error";
      if (controller.signal.aborted) {
        status.textContent = "A verificação excedeu 8 segundos. Confira sua conexão e tente novamente.";
      } else if (error.message === "unsupported") {
        status.textContent = "Este navegador não oferece a verificação SHA-256 neste contexto. Abra este preview por HTTPS em um navegador atualizado.";
      } else if (error.message === "integrity") {
        status.textContent = "O arquivo recebido não corresponde ao manifesto. O download verificado foi bloqueado. Tente novamente.";
      } else {
        status.textContent = "Não foi possível verificar o MP4. Confira sua conexão e tente novamente. Nenhum download foi marcado como verificado.";
      }
      button.textContent = "Tentar verificar novamente";
      announce(status.textContent);
    } finally {
      clearTimeout(timer);
      if (sequence === currentSequence) {
        activeController = null;
        button.disabled = false;
      }
    }
  }

  function showWalkthrough(announceChange) {
    const step = WALKTHROUGH[walkthroughStep];
    $("walkthrough-title").textContent = step.title;
    $("walkthrough-copy").textContent = step.copy;
    $("walkthrough-state").textContent = `Demonstração · ${walkthroughStep + 1} de ${WALKTHROUGH.length}`;
    $("walkthrough-next").disabled = walkthroughStep === WALKTHROUGH.length - 1;
    $("walkthrough-next").textContent = walkthroughStep === WALKTHROUGH.length - 1 ? "Fluxo explorado" : "Próxima etapa";
    document.querySelectorAll("#pipeline-steps [data-step]").forEach((item) => {
      const index = Number(item.dataset.step);
      item.dataset.state = index === walkthroughStep ? "current" : index < walkthroughStep ? "explored" : "idle";
      if (index === walkthroughStep) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });
    if (announceChange) announce(`Percurso demonstrativo, etapa ${walkthroughStep + 1}: ${step.title}. Nenhuma operação de backend foi executada.`);
  }

  document.querySelectorAll("[data-format]").forEach((button) => {
    button.addEventListener("click", () => selectFormat(button.dataset.format));
  });
  $("verify-button").addEventListener("click", verify);
  $("walkthrough-next").addEventListener("click", () => {
    walkthroughStep = Math.min(walkthroughStep + 1, WALKTHROUGH.length - 1);
    showWalkthrough(true);
  });
  $("walkthrough-reset").addEventListener("click", () => {
    walkthroughStep = 0;
    showWalkthrough(true);
  });
  $("output-video").addEventListener("error", () => {
    announce("O vídeo não pôde ser reproduzido. Tente abrir o MP4 de teste pelo link abaixo.");
  });
  window.addEventListener("pagehide", () => {
    if (activeController) activeController.abort();
    // Keep a verified URL alive for the back-forward cache and pending saves.
  });
  setRawLink();
  showWalkthrough(false);
})();
