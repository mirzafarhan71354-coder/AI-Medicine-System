self.addEventListener("install", (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
    let payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch (error) {
        payload = { body: event.data ? event.data.text() : "A medicine reminder is due." };
    }

    event.waitUntil(self.registration.showNotification(
        payload.title || "CarePoint medicine reminder",
        {
            body: payload.body || "A medicine reminder is due.",
            icon: "/static/carepoint-icon.svg",
            badge: "/static/carepoint-icon.svg",
            tag: payload.tag || "carepoint-reminder",
            data: { url: payload.url || "/dashboard" },
            actions: [{ action: "open", title: "Open dashboard" }]
        }
    ));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const destination = new URL(
        event.notification.data && event.notification.data.url || "/dashboard",
        self.location.origin
    ).href;
    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
        .then((clients) => {
            const existing = clients.find((client) => client.url.startsWith(self.location.origin));
            if (existing) {
                existing.navigate(destination);
                return existing.focus();
            }
            return self.clients.openWindow(destination);
        }));
});
