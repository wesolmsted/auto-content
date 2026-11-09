for (const button of document.querySelectorAll("button.copy")) {
  button.addEventListener("click", async () => {
    const caption = document.getElementById(button.dataset.caption);
    try {
      await navigator.clipboard.writeText(caption.textContent);
      button.textContent = "Copied";
    } catch {
      window.getSelection().selectAllChildren(caption);
      button.textContent = "Selected: copy it from the menu";
    }
  });
}

// Fetching the download approves the render, so a cancelled share sheet still counts as saved.
const saveLink = document.getElementById("save-video");
if (saveLink && navigator.canShare) {
  saveLink.addEventListener("click", async (event) => {
    event.preventDefault();
    try {
      const response = await fetch(saveLink.href);
      if (!response.ok) {
        window.location.reload();
        return;
      }
      const file = new File([await response.blob()], saveLink.getAttribute("download"), {
        type: "video/mp4",
      });
      if (!navigator.canShare({ files: [file] })) {
        window.location.href = saveLink.href;
        return;
      }
      await navigator.share({ files: [file] });
    } catch (error) {
      if (error.name !== "AbortError") {
        window.location.href = saveLink.href;
        return;
      }
    }
    window.location.reload();
  });
}
