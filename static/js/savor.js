(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const csrf = () => document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1] || $('[name=csrfmiddlewaretoken]')?.value;
  const toast = (message) => {
    const box = $('#toast');
    box.textContent = message;
    box.classList.add('show');
    clearTimeout(box.timer);
    box.timer = setTimeout(() => box.classList.remove('show'), 3200);
  };
  const post = async (url, data) => {
    const response = await fetch(url, {method: 'POST', headers: {'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest'}, body: new URLSearchParams(data), credentials: 'same-origin'});
    if (!response.ok) throw new Error('Your change couldn’t be saved. Please try again.');
    return response.json();
  };
  // A CSRF cookie is set by the base template's modal forms on every page.
  $$('[data-favorite-url]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const result = await post(button.dataset.favoriteUrl, {});
      button.classList.toggle('is-favorite', result.favorite);
      button.setAttribute('aria-pressed', String(result.favorite));
      button.setAttribute('aria-label', result.favorite ? 'Remove from favorites' : 'Add to favorites');
      $$('[data-favorite-count]').forEach(count => count.textContent = result.count);
      toast(result.favorite ? 'A little favorite, saved for later.' : 'Removed from your favorites.');
      if (window.location.pathname === '/favorites/' && !result.favorite) {
        button.closest('.recipe-card')?.remove();
        const total = $('.collection-total');
        if (total) total.textContent = $$('.recipe-card').length;
        const tabCount = $('.tab-count');
        if (tabCount) tabCount.textContent = result.count;
        if (!$$('.recipe-card').length) window.location.reload();
      }
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }));
  const searchForm = $('#recipe-filter-form');
  // Preserve search and sort when changing filters; preserve filters when searching.
  if (searchForm) {
    $('#recipe-search').addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); searchForm.requestSubmit(); }
    });
    searchForm.addEventListener('submit', event => {
      if (!event.submitter?.name) {
        const filter = new URLSearchParams(location.search).get('filter');
        if (filter) { const hidden = document.createElement('input'); hidden.type = 'hidden'; hidden.name = 'filter'; hidden.value = filter; searchForm.append(hidden); }
      }
    });
    document.addEventListener('keydown', event => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); $('#recipe-search').focus(); }
    });
  }
  $('#sort-recipes')?.addEventListener('change', event => {
    const url = new URL(location.href); url.searchParams.set('sort', event.target.value); location.href = url;
  });
  function setView(view) {
    $('#recipe-grid')?.classList.toggle('list-view', view === 'list');
    $$('[data-view]').forEach(button => { const active = button.dataset.view === view; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
    try { localStorage.setItem('savor-view', view); } catch (_) {}
  }
  $$('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  try { if (localStorage.getItem('savor-view') === 'list') setView('list'); } catch (_) {}
  let lastDialogTrigger;
  $$('[data-open-dialog]').forEach(button => button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.openDialog);
    if (!dialog) return;
    lastDialogTrigger = button;
    if (dialog.id === 'plan-dialog') {
      const form = $('#plan-form'); form.reset();
      if (button.dataset.recipe) $('#plan-recipe').value = button.dataset.recipe;
      if (button.dataset.date) $('#plan-date').value = button.dataset.date;
      if (button.dataset.meal) $('#plan-meal').value = button.dataset.meal;
      const servings = button.dataset.servings || $('#plan-recipe').selectedOptions[0]?.dataset.servings || 2;
      $('#plan-servings').value = servings;
    }
    dialog.showModal();
  }));
  $$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  $$('dialog').forEach(dialog => {
    dialog.addEventListener('click', event => { const rect = dialog.getBoundingClientRect(); if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close(); });
    dialog.addEventListener('close', () => lastDialogTrigger?.focus());
  });
  $('dialog[data-auto-open]')?.showModal();
  $('#plan-recipe')?.addEventListener('change', event => { $('#plan-servings').value = event.target.selectedOptions[0]?.dataset.servings || 2; });
  $$('[data-dismiss-message]').forEach(button => button.addEventListener('click', () => button.closest('.message').remove()));
  $$('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
  const servingForm = $('#serving-form');
  if (servingForm) {
    const input = $('#serving-count');
    const status = $('#serving-status');
    let currentServings = Number(servingForm.dataset.currentServings);
    let controller;
    let timer;
    const scaleRecipe = async () => {
      clearTimeout(timer);
      controller?.abort();
      const value = Number(input.value);
      if (!Number.isInteger(value) || value < 1 || value > 2147483647) {
        input.setAttribute('aria-invalid', 'true');
        status.textContent = 'Choose a positive whole number of servings.';
        status.classList.add('has-error');
        return;
      }
      input.removeAttribute('aria-invalid');
      status.classList.remove('has-error');
      const request = new AbortController(); controller = request;
      servingForm.setAttribute('aria-busy', 'true');
      // Keep the planner from using an amount while ingredients are updating.
      const planButton = $('.detail-plan-button'); planButton.disabled = true;
      try {
        const response = await fetch(`${servingForm.dataset.scaleUrl}?servings=${value}`, {signal: request.signal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Your ingredients couldn’t be updated. Please try again.');
        if (controller !== request) return;
        result.ingredients.forEach(ingredient => {
          const quantity = $(`[data-ingredient-id="${ingredient.id}"]`);
          quantity.textContent = ingredient.amount_display;
          quantity.title = ingredient.exact;
          $('.ingredient-unit', quantity.parentElement).textContent = ingredient.unit;
        });
        currentServings = result.servings;
        $('#recipe-stat-servings').textContent = currentServings;
        planButton.dataset.servings = currentServings;
        const cookLink = $('.detail-cook-link');
        if (cookLink) { const link = new URL(cookLink.href); link.searchParams.set('servings', currentServings); cookLink.href = link; }
        const pantryStatus = $('#recipe-pantry-status');
        if (pantryStatus && result.pantry) pantryStatus.textContent = `${result.pantry.covered_count} of ${result.pantry.total_count} ingredients covered by your pantry${result.pantry.ready ? ' · ready to cook' : ''}`;
        const url = new URL(location.href); url.searchParams.set('servings', currentServings); history.replaceState(null, '', url);
        status.textContent = `Ingredients adjusted for ${currentServings} serving${currentServings === 1 ? '' : 's'}.`;
      } catch (error) {
        if (error.name !== 'AbortError') {
          input.value = currentServings;
          status.textContent = error.message; status.classList.add('has-error');
        }
      } finally {
        if (controller === request) { servingForm.removeAttribute('aria-busy'); planButton.disabled = false; }
      }
    };
    input.addEventListener('input', () => { clearTimeout(timer); controller?.abort(); timer = setTimeout(scaleRecipe, 200); });
    input.addEventListener('change', scaleRecipe);
    servingForm.addEventListener('submit', event => { event.preventDefault(); scaleRecipe(); });
    $$('[data-serving-change]').forEach(button => button.addEventListener('click', () => {
      input.value = Math.max(1, Math.min(2147483647, (Number(input.value) || currentServings) + Number(button.dataset.servingChange)));
      scaleRecipe();
    }));
  }
  const updateProgress = () => {
    const checks = $$('[data-shopping-key]');
    const count = checks.filter(check => check.checked).length;
    $('#checked-count').textContent = count;
    $('#shopping-progress').value = count;
    $('#shopping-complete').hidden = count !== checks.length;
    const stockButton = $('#stock-checked-button'); if (stockButton) stockButton.disabled = count === 0;
  };
  $$('[data-shopping-key]').forEach(check => check.addEventListener('change', async () => {
    check.disabled = true;
    try {
      await post(check.dataset.url, {key: check.dataset.shoppingKey, week: check.dataset.week, checked: String(check.checked)});
      check.closest('.shopping-item').classList.toggle('checked', check.checked); updateProgress();
    } catch (error) { check.checked = !check.checked; toast(error.message); }
    finally { check.disabled = false; }
  }));
  $('#add-ingredient')?.addEventListener('click', () => {
    const count = $('#id_ingredients-TOTAL_FORMS');
    if (Number(count.value) >= 100) { toast('You can add up to 100 ingredients.'); return; }
    const html = $('#ingredient-template').innerHTML.replaceAll('__prefix__', count.value);
    $('#ingredient-forms').insertAdjacentHTML('beforeend', html); count.value = Number(count.value) + 1;
    $('#ingredient-forms').lastElementChild.querySelector('input[name$="-quantity"]').focus();
  });
  $('#ingredient-forms')?.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-ingredient]'); if (!button) return;
    const row = button.closest('.ingredient-form');
    row.querySelector('[name$="-DELETE"]').checked = true;
    $$('input:not([type=hidden]):not([type=checkbox])', row).forEach(input => input.required = false);
    row.classList.add('deleted');
  });
  $('#id_image')?.addEventListener('change', event => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 8 * 1024 * 1024) { toast('Please choose an image smaller than 8 MB.'); event.target.value = ''; return; }
    const image = document.createElement('img'); image.alt = 'Your recipe photo preview'; image.src = URL.createObjectURL(file);
    image.onload = () => URL.revokeObjectURL(image.src);
    $('#upload-preview').replaceChildren(image);
  });
  const closeSidebar = () => { $('#sidebar').classList.remove('is-open'); $('.sidebar-backdrop').classList.remove('is-open'); $('[data-toggle-sidebar]').setAttribute('aria-expanded', 'false'); };
  $('[data-toggle-sidebar]')?.addEventListener('click', () => { const open = $('#sidebar').classList.toggle('is-open'); $('.sidebar-backdrop').classList.toggle('is-open', open); $('[data-toggle-sidebar]').setAttribute('aria-expanded', String(open)); });
  $('[data-close-sidebar]')?.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeSidebar(); });
})();
