async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || JSON.stringify(data) || "Request failed");
  }
  return data;
}

async function submitLogin(event) {
  event.preventDefault();
  const resultEl = document.getElementById("login-result");
  const userId = document.getElementById("login-user-id").value.trim();
  const password = document.getElementById("login-password").value;
  const pin = document.getElementById("login-pin").value.trim();

  try {
    resultEl.textContent = "Logging in...";
    await fetchJson("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        password,
        pin,
      }),
    });
    window.location.replace("/ui/index.html");
  } catch (err) {
    resultEl.textContent = `Login failed: ${err.message}`;
  }
}

document.getElementById("login-form").addEventListener("submit", submitLogin);
