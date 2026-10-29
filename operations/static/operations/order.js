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
    const syncProduct = () => {
      const quantity = product.querySelector('.sale-qty');
      product.querySelector('.product-fields').hidden = !toggle.checked;
      if (!toggle.checked) quantity.value = '';
      review.hidden = true;
    };
    toggle.addEventListener('change', syncProduct);
  });
  form.querySelectorAll('.use-suggestion').forEach(button => button.addEventListener('click', () => {
    const product = button.closest('.sale-product'), toggle = product.querySelector('.product-toggle');
    const quantity = document.getElementById(button.dataset.quantity);
    toggle.checked = true; product.querySelector('.product-fields').hidden = false;
    quantity.value = quantity.dataset.suggested; quantity.focus(); review.hidden = true;
  }));
  document.querySelector('#review-sale').addEventListener('click', () => {
    if (!form.reportValidity()) return;
    const data = new FormData(form), products = data.getAll('product'), quantities = data.getAll('quantity');
    const prices = data.getAll('unit_price'), discounts = data.getAll('discount_percent'), units = data.getAll('unit');
    const target = document.querySelector('#review-lines'); target.replaceChildren(); let total = 0, count = 0;
    products.forEach((product, index) => {
      if (!quantities[index].trim()) return; const row = document.createElement('p');
      row.textContent = `${product}: ${quantities[index]} ${units[index]} · USD ${prices[index] || 'sin tarifa'} · descuento ${discounts[index]} %`;
      target.append(row); count++; total += Math.round(Number(quantities[index]) * Number(prices[index]) * (1 - Number(discounts[index]) / 100) * 100) / 100;
    });
    if (!count) target.textContent = 'Selecciona un producto e indica su cantidad.';
    document.querySelector('#review-total').textContent = `Total propuesto: USD ${total.toFixed(2)} · ${data.get('client')} · entrega solicitada ${data.get('required_date')}`;
    document.querySelector('#confirm-sale').textContent = data.get('exception_reason').trim() ? 'Solicitar aprobación de excepción' : 'Confirmar venta';
    document.querySelector('#confirm-sale').disabled = !count; review.hidden = false; review.querySelector('h2').focus();
  });
  document.querySelector('#edit-sale').addEventListener('click', () => { review.hidden = true; document.querySelector('#review-sale').focus(); });
})();
