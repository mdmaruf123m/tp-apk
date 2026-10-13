# Notification-only Render service

This folder is deployed as a **separate Render web service** from the APK builder.

## Render environment variables

Set `NOTIFICATION_API_KEY`, `FIREBASE_SERVICE_ACCOUNT_JSON`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT`, `IMGBB_API_KEY`, and `CORS_ORIGINS` in the new service. Never commit service-account JSON or private keys.

The frontend sends `Authorization: Bearer <NOTIFICATION_API_KEY>` to the admin endpoints. The service reads and writes Firestore collections `notifications`, `notification_batches`, and `push_subscriptions`.

## Endpoints

- `GET /api/health` — service health/configuration
- `POST /api/upload-image` — optional ImgBB image upload
- `GET /api/batches` — list batches
- `POST /api/batches` — create a regular or push batch
- `PATCH /api/batches/{id}` — edit a pending batch
- `DELETE /api/batches/{id}` — remove a pending batch
- `POST /api/batches/{id}/send` — start background sending
- `GET /api/batches/{id}` — batch status and progress
