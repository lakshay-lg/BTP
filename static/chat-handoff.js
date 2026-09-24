// Use normal external links so popup blockers do not depend on async clipboard work.
// Only the generic prompt is copied; source files never enter provider URLs.
const promptText = document.getElementById('normalizerPrompt');
const promptStatus = document.getElementById('promptStatus');
const promptDetails = document.getElementById('promptDetails');

async function copyNormalizerPrompt(provider) {
  try {
    await navigator.clipboard.writeText(promptText.value);
    promptStatus.textContent = provider
      ? `Prompt copied. In ${provider}, paste it and attach your original Excel. If no tab opened, allow popups or open the provider link manually.`
      : 'Prompt copied. Paste it into your chosen chat and attach your original Excel.';
  } catch {
    promptDetails.open = true;
    promptText.focus();
    promptText.select();
    promptStatus.textContent = 'Automatic copying is unavailable. Copy the selected prompt below (Ctrl+C or ⌘C), then paste it in your chat and attach the original Excel.';
  }
}

document.querySelectorAll('[data-provider]').forEach(link => {
  link.addEventListener('click', () => copyNormalizerPrompt(link.dataset.provider));
});
document.getElementById('copyPrompt').addEventListener('click', () => copyNormalizerPrompt());
