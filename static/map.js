(async function () {
  const el = document.getElementById('map');
  if (!el || !window.L) return;
  const map = L.map(el).setView([19.076, 72.8777], 11);
  const key = el.dataset.key;
  const tileUrl = 'https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png' + (key ? '?key=' + encodeURIComponent(key) : '');
  L.tileLayer(tileUrl, {maxZoom: 19, attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}).addTo(map);
  try {
    const response = await fetch('/api/chargers');
    if (!response.ok) throw new Error('Failed to load chargers');
    const chargers = await response.json();
    const bounds = [];
    chargers.forEach(c => {
      const point = [c.latitude, c.longitude]; bounds.push(point);
      const popup = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = c.title;
      const detail = document.createElement('p'); detail.textContent = c.area + ' · ' + c.connector + ' · ₹' + c.price_per_hour + '/hr';
      const link = document.createElement('a'); link.href = c.url; link.textContent = 'View & book';
      popup.append(title, detail, link);
      L.marker(point).addTo(map).bindPopup(popup);
    });
    if (bounds.length) map.fitBounds(bounds, {padding: [25,25], maxZoom: 13});
  } catch (err) { console.error(err); }
})();
