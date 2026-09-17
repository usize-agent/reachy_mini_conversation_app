// Minimal consumer for the GStreamer webrtcsink signalling protocol
// (gst-plugins-rs). Connects to the robot's producer, receives video + audio,
// and sends the local microphone back on the negotiated audio transceiver.

const RETRY_MS = 2000;

export class RobotStream extends EventTarget {
  constructor({ url, videoEl }) {
    super();
    this.url = url;
    this.videoEl = videoEl;
    this.micTrack = null;
    this.ws = null;
    this.pc = null;
    this.sessionId = null;
    this.stopped = false;
    this.retryTimer = null;
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    this.endSession();
    this.ws?.close();
  }

  // Swap the microphone track in, live, without renegotiating.
  async setMicTrack(track) {
    this.micTrack = track;
    const sender = this.audioSender();
    if (sender) await sender.replaceTrack(track);
  }

  audioSender() {
    const t = this.pc?.getTransceivers().find((t) => t.receiver.track?.kind === 'audio');
    return t?.sender ?? null;
  }

  status(state, detail = '') {
    this.dispatchEvent(new CustomEvent('status', { detail: { state, detail } }));
  }

  connect() {
    this.status('connecting', 'signalling');
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onmessage = (ev) => this.onMessage(JSON.parse(ev.data));
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.endSession(false);
      if (this.stopped) return;
      this.status('error', 'signalling server unreachable');
      this.retryTimer = setTimeout(() => this.connect(), RETRY_MS);
    };
  }

  send(msg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  async onMessage(msg) {
    switch (msg.type) {
      case 'welcome':
        this.send({ type: 'setPeerStatus', roles: ['listener'], meta: { name: 'reachy-fps' } });
        this.send({ type: 'list' });
        break;
      case 'list': {
        const producer = pickProducer(msg.producers || []);
        if (producer) this.startSession(producer.id);
        else this.status('waiting', 'no camera stream published by the daemon');
        break;
      }
      case 'peerStatusChanged':
        if (!this.sessionId && (msg.roles || []).includes('producer')) this.startSession(msg.peerId);
        break;
      case 'sessionStarted':
        this.sessionId = msg.sessionId;
        break;
      case 'peer':
        if (msg.sessionId !== this.sessionId) return;
        if (msg.sdp) await this.onRemoteSdp(msg.sdp);
        else if (msg.ice) await this.pc?.addIceCandidate(msg.ice).catch((e) => console.warn('ice', e));
        break;
      case 'endSession':
        if (msg.sessionId === this.sessionId) this.restartSession('session ended by robot');
        break;
      case 'error':
        console.warn('signalling error', msg.details);
        this.status('error', msg.details || 'signalling error');
        break;
    }
  }

  startSession(peerId) {
    if (this.sessionId || this.pendingPeer) return;
    this.pendingPeer = peerId;
    this.status('connecting', 'negotiating');
    this.send({ type: 'startSession', peerId });
  }

  restartSession(reason) {
    this.endSession(false);
    this.status('error', reason);
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => this.send({ type: 'list' }), RETRY_MS);
  }

  endSession(notify = true) {
    if (notify && this.sessionId) this.send({ type: 'endSession', sessionId: this.sessionId });
    this.pc?.close();
    this.pc = null;
    this.sessionId = null;
    this.pendingPeer = null;
    if (this.videoEl.srcObject) this.videoEl.srcObject = null;
  }

  async onRemoteSdp(sdp) {
    if (sdp.type !== 'offer') return;
    const pc = new RTCPeerConnection({ iceServers: [] });
    this.pc = pc;
    window.__pc = pc; // debugging handle
    const stream = new MediaStream();

    pc.ontrack = (ev) => {
      stream.addTrack(ev.track);
      // Teleop wants frames ASAP, not smooth playback.
      if ('jitterBufferTarget' in ev.receiver) ev.receiver.jitterBufferTarget = 0;
      if (this.videoEl.srcObject !== stream) this.videoEl.srcObject = stream;
      this.videoEl.play().catch(() => {});
    };
    pc.onicecandidate = (ev) => {
      if (!ev.candidate) return;
      this.send({
        type: 'peer',
        sessionId: this.sessionId,
        ice: { candidate: ev.candidate.candidate, sdpMLineIndex: ev.candidate.sdpMLineIndex },
      });
    };
    pc.onconnectionstatechange = () => {
      if (this.pc !== pc) return;
      const s = pc.connectionState;
      if (s === 'connected') this.status('live');
      else if (s === 'failed') this.restartSession('WebRTC connection failed');
      else if (s === 'disconnected') this.status('warn', 'connection unstable');
    };

    await pc.setRemoteDescription(sdp);
    // The daemon offers audio as sendrecv so the browser can talk back.
    for (const t of pc.getTransceivers()) {
      if (t.receiver.track?.kind !== 'audio') continue;
      t.direction = 'sendrecv';
      if (this.micTrack) await t.sender.replaceTrack(this.micTrack);
    }
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.send({ type: 'peer', sessionId: this.sessionId, sdp: { type: 'answer', sdp: answer.sdp } });
  }
}

function pickProducer(producers) {
  return producers.find((p) => p.meta?.name === 'reachymini') || producers[0] || null;
}
