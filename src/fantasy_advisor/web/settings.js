const $ = (selector) => document.querySelector(selector);

async function loadSettings() {
  const status = $('#ai-status');
  try {
    const response = await fetch('/api/ai-settings', {cache: 'no-store'});
    const settings = await response.json();
    if (!response.ok) throw new Error(settings.error || 'No se pudo leer la configuración.');
    $('#openrouter-model').value = settings.model || 'openrouter/free';
    status.textContent = settings.configured ? 'Conectada' : 'Sin configurar';
    status.classList.toggle('connected', settings.configured);
    if (settings.keySource === 'environment') {
      $('#key-hint').textContent = 'La clave viene de la variable local OPENROUTER_API_KEY; este formulario no puede verla ni borrarla.';
      $('#remove-ai-key').disabled = true;
    }
  } catch (error) {
    status.textContent = 'No disponible';
    feedback(error.message, true);
  }
}

function feedback(message, error = false) {
  const node = $('#settings-feedback');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.classList.toggle('success', !error);
}

$('#use-free-router').addEventListener('click', () => { $('#openrouter-model').value = 'openrouter/free'; });

$('#ai-settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = event.submitter;
  if (submit) submit.disabled = true;
  feedback('Guardando ajustes…');
  try {
    const response = await fetch('/api/ai-settings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({apiKey: $('#openrouter-key').value, model: $('#openrouter-model').value})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudieron guardar los ajustes.');
    $('#openrouter-key').value = '';
    $('#ai-status').textContent = result.configured ? 'Conectada' : 'Modelo guardado · falta la clave';
    $('#ai-status').classList.toggle('connected', result.configured);
    feedback(result.configured ? 'Configuración guardada en este equipo.' : 'Modelo guardado. Añade la clave API para activar el análisis.');
  } catch (error) { feedback(error.message, true); }
  finally { if (submit) submit.disabled = false; }
});

$('#remove-ai-key').addEventListener('click', async () => {
  if (!window.confirm('¿Quitar la clave de OpenRouter guardada en este equipo?')) return;
  const button = $('#remove-ai-key'); button.disabled = true;
  try {
    const response = await fetch('/api/ai-settings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model: $('#openrouter-model').value, removeKey: true})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo quitar la clave.');
    $('#ai-status').textContent = result.configured ? 'Conectada desde entorno' : 'Sin configurar';
    $('#ai-status').classList.toggle('connected', result.configured);
    feedback(result.configured ? 'La clave guardada se quitó; sigue activa la clave de entorno.' : 'Clave eliminada del Administrador de credenciales.');
  } catch (error) { feedback(error.message, true); }
  finally { button.disabled = false; }
});

$('#test-ai').addEventListener('click', async () => {
  const button = $('#test-ai'); button.disabled = true; button.textContent = 'Probando…';
  feedback('Enviando una prueba breve a OpenRouter…');
  try {
    const response = await fetch('/api/ai-test', {method: 'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Falló la prueba de conexión.');
    feedback(`Conexión correcta · modelo usado: ${result.model}`);
    $('#ai-status').textContent = 'Conectada'; $('#ai-status').classList.add('connected');
  } catch (error) { feedback(error.message, true); }
  finally { button.disabled = false; button.textContent = 'Verificar conexión'; }
});

loadSettings();
