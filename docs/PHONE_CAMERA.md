# Using an Android Phone as a Camera

Any Android phone can be an extra camera node for WAMSS. The phone runs a
camera-server app that serves a live MJPEG stream over Wi-Fi; a detector
process on a computer ingests that stream exactly like an IP camera and posts
events to the central backend. One phone = one `CAMERA_ID` node, so a second
phone is just a second node.

## 1. Install the IP Webcam app

On the phone, install **IP Webcam** by Pavel Khlebovich from the Play Store
(it is the app this setup was tested with). Any app that serves an MJPEG
stream over HTTP works the same way; only the URL format differs.

## 2. Start the server on the phone

1. Open IP Webcam and scroll to the bottom.
2. Tap **Start server**. The camera preview starts and the app shows the
   stream URLs on screen.

## 3. Note the stream URL

The app displays something like:

```
http://192.168.1.61:8080/video
```

That is the MJPEG stream: `http://<phone-ip>:8080/video`. The IP changes when
the phone rejoins Wi-Fi, so check it each time you start the server (the
port is usually `8080`).

Quick sanity check: open the URL in a browser on the detector computer. You
should see the phone's live camera image. If the browser cannot reach it,
fix the network first (see below).

## 4. Point a detector node at it

On the computer that will run the detector (it can be any machine on the
same network, including the central backend machine), set:

```bash
export CAMERA_STREAM_URL="http://192.168.1.61:8080/video"
export CAMERA_ID="PHONE_01"
export WAMSS_BACKEND_URL="http://192.168.1.20:8000/event"
python vision/multicamera.py
```

Replace the phone IP with the one the app shows and the backend URL with
your central backend's LAN address. The node ingests the phone stream and
posts events like any other camera. Full node setup is in
`docs/EDGE_DEPLOYMENT.md`.

## Network requirements

- **Same Wi-Fi is simplest.** The phone and the detector computer must be on
  the same network, and the Wi-Fi router must allow device-to-device traffic
  (most home routers do; some guest networks and office VLANs isolate
  clients. If the browser test fails, try a different network or ask the
  network admin about client isolation).
- **Different networks: use Tailscale.** Install Tailscale on the phone and
  on the detector computer and sign both into the same tailnet. Then use the
  phone's Tailscale IP in the stream URL:

  ```bash
  export CAMERA_STREAM_URL="http://100.x.y.z:8080/video"
  ```

  (Replace `100.x.y.z` with the phone's Tailscale address shown in the
  Tailscale app.) This works even when the phone is on mobile data and the
  detector is on office Wi-Fi.

## Battery and keep-awake tips

A phone serving video drains fast. Practical tips:

- Keep the phone **plugged into a charger** while it acts as a camera.
- In IP Webcam's settings, turn off the on-screen preview overlay if you do
  not need it; the stream keeps running.
- Set the phone screen timeout long or use IP Webcam's "keep screen on"
  option so Android does not suspend the app. Some phones aggressively kill
  background apps: exempt IP Webcam from battery optimization (Settings >
  Apps > IP Webcam > Battery > Unrestricted).
- Lower the stream resolution in the app settings if the detector lags; 720p
  is plenty for person detection.

## Adding a second phone

Repeat the same steps on the second phone and give the new node its own id:

```bash
export CAMERA_STREAM_URL="http://192.168.1.62:8080/video"
export CAMERA_ID="PHONE_02"
export WAMSS_BACKEND_URL="http://192.168.1.20:8000/event"
python vision/multicamera.py
```

Two nodes may run on the same computer (two terminal windows, two processes)
or on two different computers. The only hard rule is unique `CAMERA_ID` per
node.

## Limitations

- **Wi-Fi bandwidth:** MJPEG streams are uncompressed frame-by-frame JPEG,
  roughly 1-5 Mbps at 720p. Two or three phones on one access point are
  fine; a dozen may saturate it. Lower the resolution in the app if you see
  stutter.
- **Phone sleep:** if Android suspends the app, the stream dies and the
  detector logs "No frame received" until you restart the server. The
  battery tips above prevent this.
- **Changing IP:** DHCP can reassign the phone's IP. If the stream stops
  working overnight, check the app for the new IP and update
  `CAMERA_STREAM_URL`. A static DHCP reservation on the router avoids this.
- **Latency:** expect a few hundred milliseconds of delay over Wi-Fi. Fine
  for occupancy and heatmaps; not suitable for anything needing
  frame-exact timing.
