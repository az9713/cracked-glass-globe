'use strict';
const roles = {
  human: ['Human: intent and authority', 'The human chose the scene, approved each paid generation, required the existing globe as the source, and made the final model, duration, and resolution choices.'],
  astro: ['Astro: the language-model assistant', 'Astro read documentation, wrote and invoked the local client, translated intent into API arguments, tracked asynchronous jobs, and inspected the returned artifacts. It did not render the image or generate the video frames. The repository does not establish its exact runtime model/version.'],
  sdk: ['SDK: authenticated transport on the local machine', 'The Python SDK turned model IDs and JSON into HTTPS requests. The local wrapper kept credentials out of normal output, recorded accepted job IDs, prevented automatic generation retries, and allowed waiting to resume after an interruption. A dry run never contacted the generation endpoint.'],
  api: ['Higgsfield: the service boundary', 'The API exposed authentication, model endpoints, asynchronous job status, and media URLs. Scheduling, inference compute, moderation, billing, storage, and delivery are service responsibilities at a logical level. Their exact ownership and physical deployment were not observable in this run.'],
  models: ['Media models: pixels and temporal change', 'Marketing Studio Image returned the still image; its underlying model revision was not established. The Seedance 2.0 image-to-video endpoint accepted that PNG URL and a motion prompt, then returned a video. A model can reinterpret composition even when the prompt requests preservation.']
};
document.querySelectorAll('[data-role]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-role]').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
  const [title, text] = roles[button.dataset.role];
  document.getElementById('role-title').textContent = title;
  document.getElementById('role-text').textContent = text;
}));
function calculate() {
  const values = ['seconds','width','height'].map(id => Number(document.getElementById(id).value));
  const output = document.getElementById('price');
  if (values.some(n => !Number.isFinite(n) || n <= 0)) { output.textContent = 'Enter positive duration and dimensions.'; return; }
  const tokens = Math.ceil(values[0] * values[1] * values[2] * 24 / 1024);
  output.textContent = `$${(tokens * .014 / 1000).toFixed(2)} before discounts · ${tokens.toLocaleString('en-US')} video tokens`;
}
document.querySelectorAll('.inputs input').forEach(input => input.addEventListener('input', calculate));
document.getElementById('actual-dimensions').addEventListener('click', () => {
  document.getElementById('seconds').value = '10.08';
  document.getElementById('width').value = '1112';
  document.getElementById('height').value = '834';
  calculate();
});
