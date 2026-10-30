(() => {
  const form = document.querySelector('#sale-form'); if (!form) return;
  const destination = document.querySelector('#destination-form');
  if (destination) destination.addEventListener('submit', () => {
    destination.querySelectorAll('[data-carried="1"]').forEach(input => input.remove());
    for (const [name, value] of new FormData(form)) {
      if (['csrfmiddlewaretoken', 'client', 'required_date', 'suggestion_mode'].includes(name)) continue;
      const input = document.createElement('input'); input.type = 'hidden'; input.name = name;
      input.value = value; input.dataset.carried = '1'; destination.append(input);
    }
    form.querySelectorAll('.product-toggle:checked').forEach(toggle => {
      const input = document.createElement('input'); input.type = 'hidden'; input.name = 'selected_product';
      input.value = toggle.closest('.sale-product').querySelector('input[name="product"]').value;
      input.dataset.carried = '1'; destination.append(input);
    });
  });
  const review = document.querySelector('#sale-review');
  const lineFilter = document.querySelector('#product-line-filter');
  const selectionStatus = document.querySelector('#selection-status');
  const formatNumber = (value, places = 3) => Number(value).toLocaleString('es-SV', {
    maximumFractionDigits: places,
  });
  const formatMoney = value => Number(value).toLocaleString('es-SV', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  const formatDate = value => {
    const [year, month, day] = value.split('-').map(Number);
    return new Intl.DateTimeFormat('es-SV', {
      day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
    }).format(new Date(Date.UTC(year, month - 1, day)));
  };
  function updateSelectionStatus() {
    const count = form.querySelectorAll('.product-toggle:checked').length;
    if (selectionStatus) {
      selectionStatus.textContent = count === 1
        ? '1 producto seleccionado. Revisa cada cantidad y su unidad.'
        : `${count} productos seleccionados. Revisa cada cantidad y su unidad.`;
      selectionStatus.classList.toggle('selection-status-active', count > 0);
    }
  }
  function filterProducts() {
    if (!lineFilter) return;
    form.querySelectorAll('.sale-product').forEach(product => {
      product.hidden = lineFilter.value !== '*' && product.dataset.category !== lineFilter.value;
    });
  }
  if (lineFilter) lineFilter.addEventListener('change', () => { filterProducts(); review.hidden = true; });
  filterProducts();
  form.addEventListener('submit', event => { if (review.hidden) { event.preventDefault(); document.querySelector('#review-sale').click(); } });
  function exception() { form.querySelectorAll('.fixed-price').forEach(input => input.readOnly = !document.querySelector('#exception').value.trim()); }
  exception();
  form.addEventListener('input', () => { review.hidden = true; exception(); });
  form.querySelectorAll('.product-toggle').forEach(toggle => {
    const product = toggle.closest('.sale-product');
    product.querySelector('.product-fields').hidden = !toggle.checked;
    const quantity = product.querySelector('.sale-qty');
    const unit = product.querySelector('.sale-unit');
    quantity.required = toggle.checked;
    unit.required = toggle.checked;
    const syncProduct = () => {
      product.querySelector('.product-fields').hidden = !toggle.checked;
      quantity.required = toggle.checked;
      unit.required = toggle.checked;
      if (!toggle.checked) quantity.value = '';
      review.hidden = true;
      updateSelectionStatus();
    };
    toggle.addEventListener('change', syncProduct);
  });
  updateSelectionStatus();
  form.querySelectorAll('.use-suggestion').forEach(button => button.addEventListener('click', () => {
    const product = button.closest('.sale-product'), toggle = product.querySelector('.product-toggle');
    const quantity = document.getElementById(button.dataset.quantity);
    toggle.checked = true; product.querySelector('.product-fields').hidden = false;
    quantity.value = quantity.dataset.suggested; quantity.focus(); review.hidden = true;
    updateSelectionStatus();
  }));
  document.querySelector('#review-sale').addEventListener('click', () => {
    if (!form.reportValidity()) return;
    const data = new FormData(form), products = data.getAll('product'), quantities = data.getAll('quantity');
    const prices = data.getAll('unit_price'), discounts = data.getAll('discount_percent'), units = data.getAll('unit');
    const target = document.querySelector('#review-lines'); target.replaceChildren(); let total = 0, count = 0;
    products.forEach((product, index) => {
      if (!quantities[index].trim()) return; const row = document.createElement('p');
      row.textContent = `${product}: ${formatNumber(quantities[index])} ${units[index]} · USD ${formatMoney(prices[index])} · descuento ${formatNumber(discounts[index], 2)} %`;
      target.append(row); count++; total += Math.round(Number(quantities[index]) * Number(prices[index]) * (1 - Number(discounts[index]) / 100) * 100) / 100;
    });
    if (!count) target.textContent = 'Selecciona un producto e indica su cantidad.';
    document.querySelector('#review-total').textContent = `Total propuesto: USD ${formatMoney(total)} · ${data.get('client')} · entrega solicitada ${formatDate(data.get('required_date'))}`;
    const conditions = [
      data.get('payment_terms').trim() ? `Condiciones: ${data.get('payment_terms').trim()}` : '',
      data.get('note').trim() ? `Observaciones: ${data.get('note').trim()}` : '',
    ].filter(Boolean);
    document.querySelector('#review-conditions').textContent = conditions.join(' · ')
      || 'Sin condiciones comerciales u observaciones adicionales.';
    document.querySelector('#confirm-sale').textContent = data.get('exception_reason').trim() ? 'Solicitar aprobación de excepción' : 'Confirmar venta';
    document.querySelector('#confirm-sale').disabled = !count; review.hidden = false; review.querySelector('h2').focus();
  });
  document.querySelector('#edit-sale').addEventListener('click', () => { review.hidden = true; document.querySelector('#review-sale').focus(); });
})();
