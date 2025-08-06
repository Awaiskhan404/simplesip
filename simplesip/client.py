
import socket
import time
import threading
import random
import logging
import hashlib
from datetime import datetime
from collections import deque
import struct
import re
from enum import Enum

class CallState(Enum):
    IDLE = "idle"
    INVITING = "inviting" 
    RINGING = "ringing"
    CONNECTED = "connected"
    STREAMING = "streaming"


class SimpleSIPClient:
    def __init__(self, username, password, server, port=5060, local_port=5060, local_ip="0.0.0.0"):
        self.username = username
        self.password = password
        self.server = server
        self.port = port
        self.local_port = local_port
        self.local_ip = local_ip
        self.call_id = None
        self.cseq = 1
        self.tag = str(random.randint(100000, 999999))
        self.branch_prefix = "z9hG4bK"
        self.sock = None
        self.rtp_sock = None
        self.running = False
        self.auth_info = None
        self.current_transactions = {}
        self.dialogs = {}  # Track active dialogs
        self.audio_buffer = deque(maxlen=10)
        self.remote_rtp_info = None
        self.local_rtp_port = random.randint(10000, 20000)
        self.remote_tag = None
        self.local_ip = None
        self.call_state = CallState.IDLE
        
        # Audio callback system
        self.audio_received_callback = None
        self.audio_callback_format = 'pcmu'  # 'pcmu' or 'pcm'
        
        # *** CRITICAL 491 FIXES ***
        self.sent_invites = set()
        self.last_response_time = {}
        self.invite_in_progress = False
        
        # Audio configuration
        self.audio_sample_rate = 8000
        self.audio_sample_width = 2
        self.audio_channels = 1
        self.negotiated_codec = None  # Track negotiated codec
        self.negotiated_payload_type = None
        
        # RTP sequence number and timestamp
        self.rtp_seq = random.randint(0, 65535)
        self.rtp_timestamp = random.randint(0, 4294967295)
        self.rtp_ssrc = random.randint(0, 4294967295)
        
        self._last_bye_call_id = None
        self.incoming_invite_headers = None
        self.detected_rtp_profile = 'RTP/AVP'
        
        # Configure logging for errors and minimal info
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def connect(self):
        """Connect to the SIP server with enhanced RTP socket setup"""
        try:
            self.local_ip = self.get_local_ip()
            
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((self.local_ip, self.local_port))
            self.sock.settimeout(0.5)
            
            # Enable socket reuse
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Enhanced RTP socket setup for better audio flow
            self.rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rtp_sock.bind((self.local_ip, self.local_rtp_port))
            self.rtp_sock.settimeout(0.01)  # Very short timeout for responsive audio
            self.rtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Increase socket buffer sizes for audio
            try:
                self.rtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
                self.rtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            except:
                pass  # Ignore if not supported
            
            self.logger.info(f"RTP socket bound to {self.local_ip}:{self.local_rtp_port}")
            
            self.running = True
            
            # Start threads
            threading.Thread(target=self._receive_thread, daemon=True).start()
            threading.Thread(target=self._rtp_receive_thread, daemon=True).start()
            threading.Thread(target=self._audio_processing_thread, daemon=True).start()
            threading.Thread(target=self._keepalive_thread, daemon=True).start()
            
            self.register()
            
        except Exception as e:
            self.logger.error(f"Connection failed: {str(e)}")
            raise

    def _keepalive_thread(self):
        """Send periodic keepalive messages during active calls"""
        while self.running:
            time.sleep(30)  # Send keepalive every 30 seconds
            if self.call_id and self.remote_rtp_info:
                try:
                    header = struct.pack('!BBHII', 
                                        0x80, 0, self.rtp_seq, 
                                        self.rtp_timestamp, self.rtp_ssrc)
                    self.rtp_sock.sendto(header, self.remote_rtp_info)
                    self.rtp_seq = (self.rtp_seq + 1) % 65536
                except:
                    pass

    def register(self):
        """Send initial REGISTER message"""
        branch = self._generate_branch()
        call_id = f"{random.randint(100000, 999999)}@{self.local_ip}"
        
        msg = f"REGISTER sip:{self.server} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n" \
              f"To: <sip:{self.username}@{self.server}>\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {self.cseq} REGISTER\r\n" \
              f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>;expires=3600\r\n" \
              f"Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, MESSAGE, SUBSCRIBE, INFO\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Expires: 3600\r\n" \
              f"Content-Length: 0\r\n\r\n"
        
        self.current_transactions[call_id] = {
            'type': 'REGISTER',
            'start_time': datetime.now(),
            'branch': branch,
            'retries': 0,
            'cseq': self.cseq
        }
        
        self._send_message(msg)
        self.cseq += 1
    
    def query_server_capabilities(self):
        """Send OPTIONS request to query server codec capabilities"""
        branch = self._generate_branch()
        call_id = f"{random.randint(100000, 999999)}@{self.local_ip}"
        
        msg = f"OPTIONS sip:{self.server} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n" \
              f"To: <sip:{self.server}>\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {self.cseq} OPTIONS\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Accept: application/sdp\r\n" \
              f"Content-Length: 0\r\n\r\n"
        
        self.current_transactions[call_id] = {
            'type': 'OPTIONS',
            'start_time': datetime.now(),
            'branch': branch,
            'retries': 0,
            'cseq': self.cseq
        }
        
        self._send_message(msg)
        self.cseq += 1
        self.logger.info("📋 Querying server capabilities with OPTIONS request...")
        
    def _generate_sdp_offer(self, diagnostic=False):
        """Generate SDP offer with G.722 as strongly preferred codec"""
        session_id = int(time.time())
        
        if diagnostic:
            sdp = (f"v=0\r\n"
                f"o={self.username} {session_id} 1 IN IP4 {self.local_ip}\r\n"
                f"s=SIP Call\r\n"
                f"c=IN IP4 {self.local_ip}\r\n"
                f"t=0 0\r\n"
                f"m=audio {self.local_rtp_port} RTP/AVP 9 0 8 3 4 5 6 7 18 101\r\n"
                f"a=rtpmap:9 G722/8000\r\n"
                f"a=rtpmap:0 PCMU/8000\r\n"
                f"a=rtpmap:8 PCMA/8000\r\n"
                f"a=rtpmap:3 GSM/8000\r\n"
                f"a=rtpmap:4 G723/8000\r\n"
                f"a=rtpmap:5 DVI4/8000\r\n"
                f"a=rtpmap:6 DVI4/16000\r\n"
                f"a=rtpmap:7 LPC/8000\r\n"
                f"a=rtpmap:18 G729/8000\r\n"
                f"a=rtpmap:101 telephone-event/8000\r\n"
                f"a=fmtp:101 0-16\r\n"
                f"a=sendrecv\r\n")
        else:
            sdp = (f"v=0\r\n"
                f"o={self.username} {session_id} 1 IN IP4 {self.local_ip}\r\n"
                f"s=SIP Call\r\n"
                f"c=IN IP4 {self.local_ip}\r\n"
                f"t=0 0\r\n"
                f"m=audio {self.local_rtp_port} RTP/AVP 9 0 101\r\n"  # G.722 first, PCMU fallback
                f"a=rtpmap:9 G722/8000\r\n"
                f"a=rtpmap:0 PCMU/8000\r\n"
                f"a=rtpmap:101 telephone-event/8000\r\n"
                f"a=fmtp:101 0-16\r\n"
                f"a=sendrecv\r\n")
        return sdp
            
    def _parse_sdp_answer(self, sdp):
        """Parse SDP answer to get remote RTP info and negotiated codec with profile detection"""
        lines = sdp.split('\r\n')
        ip = None
        port = None
        rtp_profile = None
        ice_candidates = []
        payload_types = []
        codec_map = {}
        
        for line in lines:
            if line.startswith('c='):
                parts = line.split()
                if len(parts) >= 3:
                    ip = parts[2]
            elif line.startswith('m=audio'):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        port = int(parts[1])
                        rtp_profile = parts[2]  # RTP/AVP, RTP/AVPF, RTP/SAVPF, etc.
                        payload_types = [int(pt) for pt in parts[3:] if pt.isdigit()]
                    except ValueError:
                        continue
            elif line.startswith('a=rtpmap:'):
                parts = line[9:].split(' ', 1)
                if len(parts) == 2:
                    pt = int(parts[0])
                    codec_info = parts[1]
                    codec_name = codec_info.split('/')[0].upper()
                    codec_map[pt] = codec_name
            elif line.startswith('a=candidate:'):
                ice_candidates.append(line)
        
        # Store the detected RTP profile for use in SDP answers
        self.detected_rtp_profile = rtp_profile or 'RTP/AVP'
        
        if payload_types and codec_map:
            self.negotiated_payload_type = payload_types[0]
            print(f"Negotiated Payload Type: {self.negotiated_payload_type}")
            self.negotiated_codec = codec_map.get(self.negotiated_payload_type, 'UNKNOWN')
            print(f"Codec Map: {codec_map}")
            if self.negotiated_codec == 'G722':
                self.logger.info(f"🎵 ✅ G.722 codec negotiated! (PT {self.negotiated_payload_type}) - High quality 16kHz audio")
                self.audio_sample_rate = 16000  # G.722 uses 16kHz internally
            else:
                self.logger.info(f"🎵 📻 Fallback codec: {self.negotiated_codec} (PT {self.negotiated_payload_type}) - Standard quality")
        
        if ip and port:
            self.remote_rtp_info = (ip, port)
            self.logger.info(f"SDP accepted - RTP endpoint: {ip}:{port}")
            self.logger.info(f"🔗 RTP connection: {self.local_ip}:{self.local_rtp_port} ↔ {ip}:{port}")
            
            if rtp_profile:
                self.logger.info(f"🔒 RTP Profile: {rtp_profile}")
                if 'SAVPF' in rtp_profile or 'SAVP' in rtp_profile:
                    self.logger.warning(f"⚠️  Server wants secure RTP ({rtp_profile}) but client only supports plain RTP/AVP")
                    self.logger.warning(f"⚠️  RTP packets may not be received due to encryption/ICE requirements")
                    
                    if ice_candidates:
                        self.logger.info(f"🧊 Trying to find plain RTP endpoint from {len(ice_candidates)} ICE candidates...")
                        for candidate in ice_candidates[:3]:  # Try first 3 candidates
                            self.logger.info(f"🧊 ICE candidate: {candidate}")
            
            return True
        self.logger.error("SDP parsing failed - no valid RTP endpoint found")
        return False
    
    def _send_test_rtp_packet(self):
        """Send a test RTP packet to verify connection"""
        if not self.remote_rtp_info:
            return
            
        try:
            header = struct.pack('!BBHII', 
                                0x80,  # Version=2, Padding=0, Extension=0, CC=0
                                0,     # Marker=0, Payload Type=0 (PCMU)
                                self.rtp_seq,
                                self.rtp_timestamp,
                                self.rtp_ssrc)
            
            self.rtp_sock.sendto(header, self.remote_rtp_info)
            self.logger.info(f"📤 Test RTP packet sent to {self.remote_rtp_info}")
            
            self.rtp_seq = (self.rtp_seq + 1) % 65536
            
        except Exception as e:
            self.logger.error(f"Error sending test RTP packet: {str(e)}")
            
        import threading
        threading.Timer(2.0, self._send_multiple_rtp_tests).start()
    
    def _send_multiple_rtp_tests(self):
        """Send RTP test packets with different configurations"""
        if not self.remote_rtp_info or not self.running:
            return
            
        test_ports = [
            self.remote_rtp_info[1],  # Original port
            self.remote_rtp_info[1] + 1,  # RTCP port  
            self.remote_rtp_info[1] - 1,  # Alternative port
        ]
        
        for port_offset, port in enumerate(test_ports):
            try:
                test_endpoint = (self.remote_rtp_info[0], port)
                
                for pt in [0, 8]:  # PCMU and PCMA
                    header = struct.pack('!BBHII', 
                                        0x80,  # Version=2
                                        pt,    # Payload type
                                        self.rtp_seq + port_offset,
                                        self.rtp_timestamp,
                                        self.rtp_ssrc)
                    
                    payload = bytes([0x80] * 20)  # Short silence
                    self.rtp_sock.sendto(header + payload, test_endpoint)
                    self.logger.info(f"🔍 Test RTP PT{pt} sent to {test_endpoint}")
                    
                time.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"Error in RTP test to {test_endpoint}: {str(e)}")
    
    def send_audio(self, audio_data):
        """Send audio data as-is (assumes pre-encoded mulaw 8kHz)"""
        if not self.remote_rtp_info or not audio_data or not self.running:
            return
            
        try:
            payload_type = self.negotiated_payload_type or 0
            samples_per_packet = 160  # 20ms at 8kHz
            chunk_size = 160  # mulaw: 1 byte per sample
            
            self.logger.debug(f"Audio: {len(audio_data)} bytes, {chunk_size} bytes per packet")
            
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i+chunk_size]
                if not chunk:
                    continue
                    
                header = struct.pack('!BBHII', 
                                    0x80,  # Version=2, P=0, X=0, CC=0
                                    payload_type,  # Use negotiated payload type
                                    self.rtp_seq,
                                    self.rtp_timestamp,
                                    self.rtp_ssrc)
                
                self.rtp_sock.sendto(header + chunk, self.remote_rtp_info)
                
                self.rtp_seq = (self.rtp_seq + 1) % 65536
                self.rtp_timestamp += samples_per_packet
                
                time.sleep(0.02)  # 20ms timing
                
        except Exception as e:
            self.logger.error(f"Error sending RTP: {str(e)}")
    
    def _handle_pcmu_payload(self, payload, timestamp):
        """Process PCMU (G.711 μ-law) audio with jitter buffer"""
        if not payload:
            return
            
        # pcm_data = self._ulaw_to_pcm(payload)
        
        # self._add_to_jitter_buffer(pcm_data, timestamp)
        self.audio_received_callback(payload, 'mulaw', timestamp)
    
    def _handle_pcma_payload(self, payload, timestamp):
        """Process PCMA (G.711 A-law) audio with jitter buffer"""
        if not payload:
            return
            
        pcm_data = self._alaw_to_pcm(payload)
        
        self._add_to_jitter_buffer(pcm_data, timestamp)
    
    def _handle_g722_payload(self, payload, timestamp):
        """Process G.722 audio with jitter buffer"""
        if not payload:
            return
            
        pcm_data = self._g722_decode(payload)
        
        self._add_to_jitter_buffer(pcm_data, timestamp)
    
    def _handle_dtmf_payload(self, payload):
        """Process DTMF payload (RFC2833)"""
        if not payload or len(payload) < 4:
            return
            
        # Parse DTMF event
        event, flags, duration = struct.unpack('!BBH', payload[:4])
        volume = flags & 0x3F
        end_flag = (flags & 0x80) != 0
        
        if not end_flag:  # Start of DTMF
            dtmf_chars = '0123456789*#ABCD'
            if event < len(dtmf_chars):
                char = dtmf_chars[event]
                self.logger.info(f"🔢 DTMF: {char} (volume: {volume})")
        
    def _add_to_jitter_buffer(self, pcm_data, timestamp):
        """Manage jitter buffer for smooth playback"""
        now = time.time() * 1000  # Current time in ms
        
        if not hasattr(self, '_first_rtp_timestamp'):
            self._first_rtp_timestamp = timestamp
            self._first_rtp_time = now
            
        time_offset = (timestamp - self._first_rtp_timestamp) / 8  # 8kHz = 8000 samples/sec
        play_time = self._first_rtp_time + time_offset
        
        current_delay = max(0, play_time - now)
        target_delay = 50  # ms - adjust based on network conditions
        
        if current_delay < target_delay:
            play_time = now
        elif current_delay > target_delay * 2:
            self._first_rtp_time -= (current_delay - target_delay)
            play_time = self._first_rtp_time + time_offset
        
        if self.audio_received_callback:
            try:
                self.audio_received_callback(pcm_data, 'pcm', play_time)
            except Exception as e:
                self.logger.error(f"Audio callback error: {str(e)}")
                
    def _send_test_rtp_packet(self):
        """Send enhanced test RTP packets to establish audio flow"""
        if not self.remote_rtp_info:
            return
            
        try:
            # Send multiple test packets with different payload types
            for pt in [0, 8]:  # PCMU and PCMA
                header = struct.pack('!BBHII', 
                                    0x80,  # Version=2, Padding=0, Extension=0, CC=0
                                    pt,    # Payload type
                                    self.rtp_seq,
                                    self.rtp_timestamp,
                                    self.rtp_ssrc)
                
                # Add some silence payload (20ms worth)
                if pt == 0:  # PCMU
                    payload = bytes([0xFF] * 160)  # μ-law silence
                else:  # PCMA
                    payload = bytes([0xD5] * 160)  # A-law silence
                
                self.rtp_sock.sendto(header + payload, self.remote_rtp_info)
                self.logger.info(f"📤 Enhanced test RTP PT{pt} sent to {self.remote_rtp_info}")
                
                self.rtp_seq = (self.rtp_seq + 1) % 65536
                self.rtp_timestamp = (self.rtp_timestamp + 160) % 4294967296
                
                time.sleep(0.02)  # 20ms between packets
            
            # Send periodic test packets
            threading.Timer(2.0, self._send_periodic_audio).start()
            
        except Exception as e:
            self.logger.error(f"Error sending test RTP packet: {str(e)}")
    
    def _send_periodic_audio(self):
        """Send periodic audio to keep RTP flow alive"""
        if not self.remote_rtp_info or not self.running or self.call_state != CallState.CONNECTED:
            return
            
        try:
            # Send comfort noise/silence every 2 seconds
            pt = self.negotiated_payload_type or 0
            
            header = struct.pack('!BBHII', 
                                0x80,  # Version=2
                                pt,    # Use negotiated payload type
                                self.rtp_seq,
                                self.rtp_timestamp,
                                self.rtp_ssrc)
            
            # Generate silence based on codec
            if pt == 0:  # PCMU
                payload = bytes([0xFF] * 160)  # μ-law silence
            elif pt == 8:  # PCMA
                payload = bytes([0xD5] * 160)  # A-law silence
            else:
                payload = bytes([0x00] * 160)  # Generic silence
            
            self.rtp_sock.sendto(header + payload, self.remote_rtp_info)
            self.logger.debug(f"🔊 Periodic audio sent (PT {pt})")
            
            self.rtp_seq = (self.rtp_seq + 1) % 65536
            self.rtp_timestamp = (self.rtp_timestamp + 160) % 4294967296
            
            # Schedule next periodic audio
            threading.Timer(2.0, self._send_periodic_audio).start()
            
        except Exception as e:
            self.logger.error(f"Error sending periodic audio: {str(e)}")
    
    def _rtp_receive_thread(self):
        """Enhanced RTP receive thread with better packet handling"""
        self.logger.info("🎙️ Enhanced RTP receive thread started")
        
        last_seq = None
        packets_received = 0
        
        while self.running:
            try:
                data, addr = self.rtp_sock.recvfrom(2048)
                packets_received += 1
                
                if len(data) < 12:  # Minimum RTP header size
                    continue
                    
                header = struct.unpack('!BBHII', data[:12])
                version = (header[0] >> 6) & 0x03
                padding = (header[0] >> 5) & 0x01
                extension = (header[0] >> 4) & 0x01
                csrc_count = header[0] & 0x0F
                marker = (header[1] >> 7) & 0x01
                payload_type = header[1] & 0x7F
                sequence = header[2]
                timestamp = header[3]
                ssrc = header[4]
                
                # Log first few packets and then periodically
                if packets_received <= 5 or packets_received % 100 == 0:
                    self.logger.info(f"🎙️ RTP received: PT={payload_type}, seq={sequence}, from {addr}")
                
                if last_seq is not None:
                    diff = (sequence - last_seq) % 65536
                    if diff > 1 and diff < 1000:  # Reasonable packet loss detection
                        self.logger.debug(f"Packet loss detected: {diff-1} packets")
                        
                last_seq = sequence
                
                payload = data[12+csrc_count*4:]  # Skip CSRC if present
                
                if payload_type == 0:  # PCMU
                    self._handle_pcmu_payload(payload, timestamp)
                elif payload_type == 8:  # PCMA
                    self._handle_pcma_payload(payload, timestamp)
                elif payload_type == 9:  # G.722
                    self._handle_g722_payload(payload, timestamp)
                elif payload_type == 101:  # DTMF
                    self._handle_dtmf_payload(payload)
                else:
                    self.logger.debug(f"Unknown payload type: {payload_type}")
                    
                # Update call state
                if self.call_state == CallState.CONNECTED:
                    self.call_state = CallState.STREAMING
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.logger.error(f"RTP receive error: {str(e)}")
                time.sleep(0.01)
                
    def _audio_processing_thread(self):
        """Thread to process incoming audio data and trigger callbacks"""
        while self.running:
            if self.audio_buffer:
                pcmu_data = self.audio_buffer.popleft()
                
                if self.audio_received_callback:
                    try:
                        if self.audio_callback_format == 'pcm':
                            pcm_data = self._ulaw_to_pcm(pcmu_data)
                            self.audio_received_callback(pcm_data, 'pcm')
                        elif self.audio_callback_format == 'mulaw':
                            pcm_data = self._pcm_to_ulaw(pcmu_data)
                            self.audio_received_callback(pcm_data, 'mulaw')
                        elif self.get_audio_config()['codec'] == 'g722':
                            g722_data = self._g722_decode(pcmu_data)
                            self.audio_received_callback(g722_data, 'g722')
                        elif self.get_audio_config()['codec'] == 'pcmu':
                            self.audio_received_callback(pcmu_data, 'pcmu')
                    except Exception as e:
                        self.logger.error(f"Error in audio callback: {str(e)}")
                        
            time.sleep(0.02)
    
    def set_audio_callback(self, callback_func, format='pcmu'):
        """Set callback function for received audio data
        
        Args:
            callback_func: Function to call when audio is received
                          Function signature: callback_func(audio_data, format)
            format: 'pcmu' for raw μ-law data, 'pcm' for 16-bit linear PCM
        """
        self.audio_received_callback = callback_func
        self.audio_callback_format = format
        self.logger.info(f"📻 Audio callback registered (format: {format})")
        
        
        if self.remote_rtp_info and self.call_state in [CallState.CONNECTED, CallState.STREAMING]:
            self.logger.info("🔊 Sending test audio after callback registration...")
            self._send_test_rtp_packet()
    
    def remove_audio_callback(self):
        """Remove audio callback"""
        self.audio_received_callback = None
        self.logger.info("📻 Audio callback removed")
    
    def _ulaw_to_pcm(self, ulaw_data):
        """Convert μ-law (PCMU) to 16-bit linear PCM"""
        try:
            import audioop
            return audioop.ulaw2lin(ulaw_data, 2)  # 2 = 16-bit output
            
        except ImportError:
            import numpy as np
            
            ulaw_samples = np.frombuffer(ulaw_data, dtype=np.uint8)
            pcm_samples = []
            
            for ulaw_byte in ulaw_samples:
                ulaw_byte = int(ulaw_byte) ^ 0xFF
                
                sign = ulaw_byte & 0x80
                exp = (ulaw_byte & 0x70) >> 4
                mantissa = ulaw_byte & 0x0F
                
                if exp == 0:
                    linear = int((mantissa << 4) + 0x84)
                else:
                    linear = int(((mantissa << 4) + 0x84) << (exp - 1))
                
                linear = int(linear - 0x84)
                
                if sign:
                    linear = -linear
                    
                linear = max(-32768, min(32767, linear))
                pcm_samples.append(linear)
            
            return np.array(pcm_samples, dtype=np.int16).tobytes()
    
    def _g722_encode(self, pcm_data):
        """Encode 16-bit PCM to G.722 format"""
        import numpy as np
    
        samples = np.frombuffer(pcm_data, dtype=np.int16)
        
        downsampled = samples[::2]
        
        quantized = np.clip(downsampled >> 8, -128, 127).astype(np.int8)
        
        return quantized.tobytes()
    
    def _g722_decode(self, g722_data):
        """Decode G.722 format to 16-bit PCM"""
        import numpy as np
    
        quantized = np.frombuffer(g722_data, dtype=np.int8)
        
        # Expand and upsample
        expanded = quantized.astype(np.int16) << 8
        upsampled = np.repeat(expanded, 2)  # 8kHz → 16kHz
        
        return upsampled.tobytes()
    
    def _pcm_to_ulaw(self, pcm_data):
        """Convert 16-bit PCM to μ-law format using standard algorithm"""
        import numpy as np
        
        try:
            import audioop
            return audioop.lin2ulaw(pcm_data, 2)  # 2 = 16-bit samples
            
        except ImportError:
            pcm_samples = np.frombuffer(pcm_data, dtype=np.int16)
            
            ulaw_table = [
                0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
                4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
                5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
                5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
                6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
                6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
                6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
                6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
                7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7
            ]
            
            ulaw_samples = []
            BIAS = 0x84
            
            for sample in pcm_samples:
                sample = int(sample)
                
                sign = 0x80 if sample < 0 else 0x00
                if sample < 0:
                    sample = -sample
                
                sample = min(sample + BIAS, 0x7FFF)
                
                if sample < 0x100:
                    segment = ulaw_table[sample >> 2]
                    mantissa = (sample >> 1) & 0x0F
                else:
                    segment = ulaw_table[(sample >> 6) & 0xFF] + 1
                    if segment >= 8:
                        segment = 7
                    mantissa = (sample >> (segment + 2)) & 0x0F
                
                ulaw_byte = (sign | (segment << 4) | mantissa) ^ 0xFF
                ulaw_samples.append(ulaw_byte)
            
            return bytes(ulaw_samples)
    
    def _pcm_to_alaw(self, pcm_data):
        """Convert 16-bit PCM to A-law format"""
        import numpy as np
        
        pcm_samples = np.frombuffer(pcm_data, dtype=np.int16)
        alaw_samples = []
        
        for sample in pcm_samples:
            sign = 0x80 if sample < 0 else 0x00
            if sample < 0:
                sample = -sample
            sample = min(sample, 32635)  # Clip
            
            if sample < 256:
                alaw_byte = sample >> 4
            else:
                exp = 7
                while exp > 0 and sample < (0x1 << (exp + 7)):
                    exp -= 1
                
                mantissa = (sample >> (exp + 3)) & 0x0F
                alaw_byte = (exp << 4) | mantissa
            
            alaw_byte = (alaw_byte | sign) ^ 0x55
            alaw_samples.append(alaw_byte)
        
        return bytes(alaw_samples)
    
    def _alaw_to_pcm(self, alaw_data):
        """Convert A-law to 16-bit linear PCM"""
        import numpy as np
        
        alaw_samples = np.frombuffer(alaw_data, dtype=np.uint8)
        pcm_samples = []
        
        for alaw_byte in alaw_samples:
            alaw_byte = int(alaw_byte) ^ 0x55
            
            sign = alaw_byte & 0x80
            exp = (alaw_byte & 0x70) >> 4
            mantissa = alaw_byte & 0x0F
            
            if exp == 0:
                linear = int((mantissa << 4) + 8)
            else:
                linear = int(((mantissa << 4) + 0x108) << (exp - 1))
            
            if sign:
                linear = -linear
                
            linear = max(-32768, min(32767, linear))
            pcm_samples.append(linear)
        
        return np.array(pcm_samples, dtype=np.int16).tobytes()

    def get_audio_config(self):
        """Get audio configuration based on negotiated codec"""
        codec = self.negotiated_codec or 'PCMU'
        payload_type = self.negotiated_payload_type or 0
        
        if codec == 'G722':
            return {
                'codec': codec,
                'payload_type': payload_type,
                'sample_rate': 16000,  # G.722 internal sampling rate
                'rtp_clock_rate': 8000,  # G.722 RTP clock rate
                'frame_size': 320,  # 20ms at 16kHz
                'rtp_frame_size': 160,  # RTP timestamp increment
                'encoding': 'g722',
                'chunk_size': 320  # PCM samples per 20ms frame
            }
        else:  # PCMU, PCMA
            return {
                'codec': codec,
                'payload_type': payload_type,
                'sample_rate': 8000,
                'rtp_clock_rate': 8000,
                'frame_size': 160,  # 20ms at 8kHz
                'rtp_frame_size': 160,
                'encoding': 'pcmu' if codec == 'PCMU' else 'pcma',
                'chunk_size': 160  # PCM samples per 20ms frame
            }

    def _parse_sip_message(self, message):
        """Parse SIP message headers into a dictionary"""
        lines = message.split('\r\n')
        headers = {}
        
        if lines:
            headers['start_line'] = lines[0]
        
        for line in lines[1:]:
            if ':' in line:
                key, value = line.split(':', 1)
                headers[key.strip().lower()] = value.strip()
        
        parts = message.split('\r\n\r\n', 1)
        if len(parts) > 1:
            headers['body'] = parts[1]
        
        return headers

    def _send_response(self, request_headers, status_code, reason_phrase, additional_headers=None, body=None):
        """Send a SIP response"""
        via = request_headers.get('via', '')
        from_header = request_headers.get('from', '')
        to_header = request_headers.get('to', '')
        call_id = request_headers.get('call-id', '')
        cseq = request_headers.get('cseq', '')
        
        if status_code == 200 and 'tag=' not in to_header:
            to_header += f';tag={self.tag}'
        
        response = f"SIP/2.0 {status_code} {reason_phrase}\r\n"
        response += f"Via: {via}\r\n"
        response += f"From: {from_header}\r\n"
        response += f"To: {to_header}\r\n"
        response += f"Call-ID: {call_id}\r\n"
        response += f"CSeq: {cseq}\r\n"
        
        if additional_headers:
            for header, value in additional_headers.items():
                response += f"{header}: {value}\r\n"
        
        if body:
            response += f"Content-Type: application/sdp\r\n"
            response += f"Content-Length: {len(body)}\r\n\r\n{body}"
        else:
            response += "Content-Length: 0\r\n\r\n"
        
        self._send_message(response)

    def send_ack(self, invite_headers):
        """Send ACK message with proper dialog information"""
        to_header = invite_headers.get('to', '')
        from_header = invite_headers.get('from', '')
        call_id = invite_headers.get('call-id', '')
        
        remote_tag = None
        if 'tag=' in to_header:
            remote_tag = to_header.split('tag=')[1].split(';')[0].split('>')[0]
        
        cseq_header = invite_headers.get('cseq', '')
        cseq_num = cseq_header.split()[0] if cseq_header else str(self.cseq)
        
        contact = invite_headers.get('contact', '')
        if contact and '<sip:' in contact:
            request_uri = contact.split('<')[1].split('>')[0]
        else:
            request_uri = f"sip:{self.username}@{self.server}"
        
        branch = self._generate_branch()
        
        msg = f"ACK {request_uri} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: {from_header}\r\n" \
              f"To: {to_header}\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {cseq_num} ACK\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Content-Length: 0\r\n\r\n"
        
        self._send_message(msg)
        
        self.dialogs[call_id] = {
            'local_tag': self.tag,
            'remote_tag': remote_tag,
            'remote_uri': request_uri
        }
    
    def answer_call(self, request_headers):
        """Answer an incoming call with proper SDP negotiation"""
        
        # Generate SDP answer based on the offer received
        sdp_body = self._generate_sdp_answer(request_headers.get('body', ''))
        
        additional_headers = {
            'Contact': f'<sip:{self.username}@{self.local_ip}:{self.local_port}>',
            'User-Agent': 'BetterSIPClient/1.0'
        }
        
        # Send 200 OK with SDP
        self._send_response(request_headers, 200, 'OK', additional_headers, sdp_body)
        self.logger.info(f"📤 Sent 200 OK with SDP answer")
        
    
    def _generate_sdp_answer(self, sdp_offer=None):
        """Generate SDP answer that properly handles AVPF requirements"""
        
        # Parse the offer to determine RTP profile and supported codecs
        offered_codecs = []
        offered_profile = 'RTP/AVP'  # Default fallback
        
        if sdp_offer:
            offered_codecs = self._parse_offered_codecs(sdp_offer)
            offered_profile = self._extract_rtp_profile(sdp_offer)
            self.logger.info(f"🎵 Offered codecs: {offered_codecs}")
            self.logger.info(f"🔒 Offered RTP profile: {offered_profile}")
        
        session_id = int(time.time())
        
        if 'SAVP' in offered_profile or 'SAVPF' in offered_profile:
            rtp_profile = 'RTP/AVP'  # Force plain RTP for compatibility
            self.logger.info(f"🔧 Server requested {offered_profile}, responding with plain RTP/AVP for compatibility")
        else:
            rtp_profile = offered_profile
            self.logger.info(f"🔧 Responding with matching profile: {rtp_profile}")
        
        # CRITICAL FIX: Match the offered profile exactly
        # Asterisk with AVPF enabled REQUIRES AVPF response
        rtp_profile = offered_profile
        
        self.logger.info(f"🔧 Responding with matching profile: {rtp_profile}")
        
        # Select codecs that are common between offer and our capabilities
        # Prioritize in order: PCMU, PCMA, G722, then DTMF
        supported_codecs = []
        
        if any('PCMU' in codec for codec in offered_codecs):
            supported_codecs.append("0")
        if any('PCMA' in codec for codec in offered_codecs):
            supported_codecs.append("8")
        if any('G722' in codec for codec in offered_codecs):
            supported_codecs.append("9")
        
        # Always add DTMF if offered
        if any('TELEPHONE-EVENT' in codec for codec in offered_codecs):
            supported_codecs.append("101")
        
        # Fallback to basic codecs if nothing matched
        if not supported_codecs:
            supported_codecs = ["0", "8", "101"]
        
        codec_list = " ".join(supported_codecs)
        
        # Generate SDP answer with matching profile
        sdp = (f"v=0\r\n"
            f"o={self.username} {session_id} 1 IN IP4 {self.local_ip}\r\n"
            f"s=SIP Call\r\n"
            f"c=IN IP4 {self.local_ip}\r\n"
            f"t=0 0\r\n"
            f"m=audio {self.local_rtp_port} {rtp_profile} {codec_list}\r\n")
        
        # Add codec mappings for supported codecs
        if "0" in supported_codecs:
            sdp += f"a=rtpmap:0 PCMU/8000\r\n"
        if "8" in supported_codecs:
            sdp += f"a=rtpmap:8 PCMA/8000\r\n"
        if "9" in supported_codecs:
            sdp += f"a=rtpmap:9 G722/8000\r\n"
        if "101" in supported_codecs:
            sdp += f"a=rtpmap:101 telephone-event/8000\r\n"
            sdp += f"a=fmtp:101 0-16\r\n"
        
        # Add required attributes based on profile
        if 'AVPF' in rtp_profile:
            # AVPF requires rtcp-mux
            sdp += f"a=rtcp-mux\r\n"
            
            # Add minimal AVPF feedback attributes
            if "0" in supported_codecs:
                sdp += f"a=rtcp-fb:0 ccm fir\r\n"
            if "8" in supported_codecs:
                sdp += f"a=rtcp-fb:8 ccm fir\r\n"
            if "9" in supported_codecs:
                sdp += f"a=rtcp-fb:9 ccm fir\r\n"
        
        sdp += f"a=sendrecv\r\n"
        
        self.logger.info(f"🎵 Generated SDP answer with profile: {rtp_profile}")
        return sdp
    
    def _extract_rtp_profile(self, sdp_offer):
        """Extract RTP profile from SDP offer"""
        lines = sdp_offer.split('\r\n')
        
        for line in lines:
            if line.startswith('m=audio'):
                parts = line.split()
                if len(parts) >= 3:
                    return parts[2]  # Return the RTP profile
        
        return 'RTP/AVP'
    
    def _parse_offered_codecs(self, sdp_offer):
        """Parse codecs offered in incoming SDP"""
        offered_codecs = []
        lines = sdp_offer.split('\r\n')
        
        for line in lines:
            if line.startswith('a=rtpmap:'):
                parts = line[9:].split(' ', 1)
                if len(parts) == 2:
                    pt = parts[0]
                    codec_info = parts[1]
                    codec_name = codec_info.split('/')[0].upper()
                    offered_codecs.append(f"{codec_name}({pt})")
        
        return offered_codecs
    
    
    def _handle_notify(self, message, headers):
        """Handle NOTIFY messages (typically for call status updates)"""
        
        # Send 200 OK response for NOTIFY
        additional_headers = {
            'User-Agent': 'BetterSIPClient/1.0'
        }
        self._send_response(headers, 200, 'OK', additional_headers)
        
        # Log the NOTIFY content for debugging
        event_header = headers.get('event', 'unknown')
        self.logger.info(f"📢 NOTIFY received - Event: {event_header}")
        
    def _cleanup_call_state(self):
        """Enhanced call state cleanup"""
        if self.call_id:
            invite_keys_to_remove = [k for k in self.sent_invites if self.call_id in k]
            for k in invite_keys_to_remove:
                self.sent_invites.discard(k)
        
        # Clear state variables
        self.call_id = None
        self.remote_rtp_info = None
        self.remote_tag = None
        self.invite_in_progress = False
        self.call_state = CallState.IDLE
        
        # Clear any stored invite headers
        if hasattr(self, 'incoming_invite_headers'):
            delattr(self, 'incoming_invite_headers')
        
        # Clear last BYE tracking
        if hasattr(self, '_last_bye_call_id'):
            delattr(self, '_last_bye_call_id')
    
    def make_call(self, dest_number):
        """*** FIXED: Initiate a call with duplicate prevention ***"""
        if self.invite_in_progress:
            return
            
        if self.call_id and self.call_state != CallState.IDLE:
            return
        
        self.call_id = f"{random.randint(100000, 999999)}@{self.local_ip}"
        invite_key = f"{self.call_id}:{dest_number}"
        
        if invite_key in self.sent_invites:
                return
        
        self.invite_in_progress = True
        self.sent_invites.add(invite_key)
        
        self.last_invite_time = datetime.now()
        
        branch = self._generate_branch()
        sdp_body = self._generate_sdp_offer()
        
        msg = f"INVITE sip:{dest_number}@{self.server} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n" \
              f"To: <sip:{dest_number}@{self.server}>\r\n" \
              f"Call-ID: {self.call_id}\r\n" \
              f"CSeq: {self.cseq} INVITE\r\n" \
              f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n" \
              f"Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, MESSAGE, SUBSCRIBE, INFO\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Supported: replaces, timer\r\n" \
              f"Content-Type: application/sdp\r\n" \
              f"Content-Length: {len(sdp_body)}\r\n\r\n" \
              f"{sdp_body}"
        
        self.current_transactions[self.call_id] = {
            'type': 'INVITE',
            'start_time': datetime.now(),
            'branch': branch,
            'dest_number': dest_number,
            'retries': 0,
            'cseq': self.cseq,
            'invite_key': invite_key,
            'headers': self._parse_sip_message(msg)
        }
        
        self._send_message(msg)
        self.cseq += 1
        self.call_state = CallState.INVITING
        self.logger.info(f"📞 CALL STATUS: INVITED - Call to {dest_number} initiated")

    def _handle_401_unauthorized(self, message):
        """Handle 401 Unauthorized response with better parsing"""
        headers = self._parse_sip_message(message)
        www_auth = headers.get('www-authenticate', '')
        
        if not www_auth:
            self.logger.error("No WWW-Authenticate header in 401 response")
            return
        
        auth_params = {}
        if www_auth.startswith('Digest'):
            auth_string = www_auth[6:].strip()
            
            parts = re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', auth_string)
            for key, quoted_val, unquoted_val in parts:
                auth_params[key.lower()] = quoted_val or unquoted_val
        
        self.auth_info = {
            'realm': auth_params.get('realm', ''),
            'nonce': auth_params.get('nonce', ''),
            'algorithm': auth_params.get('algorithm', 'MD5'),
            'qop': auth_params.get('qop', ''),
            'opaque': auth_params.get('opaque', '')
        }
        
        call_id = headers.get('call-id', '')
        
        if call_id in self.current_transactions:
            transaction = self.current_transactions[call_id]
            if transaction['type'] == 'INVITE':
                if 'invite_key' in transaction:
                    self.sent_invites.discard(transaction['invite_key'])
                self._retry_invite_with_auth(transaction['dest_number'], call_id)
            elif transaction['type'] == 'REGISTER':
                self._retry_register_with_auth(call_id)

    def _calculate_auth_response(self, method, uri):
        """Calculate SIP Digest authentication response"""
        if not self.auth_info:
            return None
            
        ha1 = hashlib.md5(
            f"{self.username}:{self.auth_info['realm']}:{self.password}".encode()
        ).hexdigest()
        
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        
        if self.auth_info.get('qop'):
            nc = "00000001"
            cnonce = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
            response = hashlib.md5(
                f"{ha1}:{self.auth_info['nonce']}:{nc}:{cnonce}:{self.auth_info['qop']}:{ha2}".encode()
            ).hexdigest()
        else:
            response = hashlib.md5(
                f"{ha1}:{self.auth_info['nonce']}:{ha2}".encode()
            ).hexdigest()
        
        return response

    def _retry_invite_with_auth(self, dest_number, call_id):
        """Retry INVITE with authentication"""
        if not self.auth_info:
            self.logger.error("No auth info available for retry")
            return
            
        branch = self._generate_branch()
        uri = f"sip:{dest_number}@{self.server}"
        response = self._calculate_auth_response("INVITE", uri)
        
        if not response:
            self.logger.error("Failed to calculate auth response")
            return
        
        sdp_body = self._generate_sdp_offer()
        
        auth_header = f'Digest username="{self.username}", realm="{self.auth_info["realm"]}", ' \
                     f'nonce="{self.auth_info["nonce"]}", uri="{uri}", ' \
                     f'response="{response}", algorithm={self.auth_info["algorithm"]}'
        
        if self.auth_info.get('opaque'):
            auth_header += f', opaque="{self.auth_info["opaque"]}"'
        
        if self.auth_info.get('qop'):
            nc = "00000001"
            cnonce = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
            auth_header += f', qop={self.auth_info["qop"]}, nc={nc}, cnonce="{cnonce}"'
        
        msg = f"INVITE {uri} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n" \
              f"To: <sip:{dest_number}@{self.server}>\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {self.cseq} INVITE\r\n" \
              f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>\r\n" \
              f"Authorization: {auth_header}\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Content-Type: application/sdp\r\n" \
              f"Content-Length: {len(sdp_body)}\r\n\r\n" \
              f"{sdp_body}"
        
        invite_key_auth = f"{call_id}:{dest_number}:auth"
        self.sent_invites.add(invite_key_auth)
        
        self.current_transactions[call_id]['retries'] = 1
        self.current_transactions[call_id]['branch'] = branch
        self.current_transactions[call_id]['invite_key'] = invite_key_auth
        
        self._send_message(msg)
        self.cseq += 1

    def _retry_register_with_auth(self, call_id):
        """Retry REGISTER with authentication"""
        if not self.auth_info:
            self.logger.error("No auth info available for retry")
            return
            
        branch = self._generate_branch()
        uri = f"sip:{self.server}"
        response = self._calculate_auth_response("REGISTER", uri)
        
        if not response:
            self.logger.error("Failed to calculate auth response")
            return
        
        auth_header = f'Digest username="{self.username}", realm="{self.auth_info["realm"]}", ' \
                     f'nonce="{self.auth_info["nonce"]}", uri="{uri}", ' \
                     f'response="{response}", algorithm={self.auth_info["algorithm"]}"'
        
        if self.auth_info.get('opaque'):
            auth_header += f', opaque="{self.auth_info["opaque"]}"'
        
        msg = f"REGISTER {uri} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch};rport\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n" \
              f"To: <sip:{self.username}@{self.server}>\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {self.cseq} REGISTER\r\n" \
              f"Contact: <sip:{self.username}@{self.local_ip}:{self.local_port}>;expires=3600\r\n" \
              f"Authorization: {auth_header}\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Expires: 3600\r\n" \
              f"Content-Length: 0\r\n\r\n"
        
        self.current_transactions[call_id]['retries'] = 1
        self.current_transactions[call_id]['branch'] = branch
        
        self._send_message(msg)
        self.cseq += 1

    def _receive_thread(self):
        """Enhanced thread to handle incoming SIP messages"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
                message = data.decode()
                first_line = message.split('\r\n')[0] if message else ''
                self.logger.info(f"📥 SIP MESSAGE: {first_line}")
                self._handle_message(message)
            except socket.timeout:
                self._handle_timeouts()
                continue
            except Exception as e:
                if self.running:
                    self.logger.error(f"Error in receive thread: {str(e)}")
                time.sleep(1)

    def _handle_message(self, message):
        """*** ENHANCED: Handle 491 Request Pending responses ***"""
        if not message:
            return
            
        headers = self._parse_sip_message(message)
        first_line = headers.get('start_line', '')
        
        if "SIP/2.0 491 Request Pending" in first_line:
            call_id = headers.get('call-id', '')
            
            self._send_491_ack(headers)
            
            if call_id in self.current_transactions:
                transaction = self.current_transactions[call_id]
                if 'invite_key' in transaction:
                    self.sent_invites.discard(transaction['invite_key'])
                if self.call_state == CallState.INVITING:
                    del self.current_transactions[call_id]
                else:
                    transaction['retries'] = 999  # Prevent further retries
            
            if self.call_state == CallState.INVITING:
                self.invite_in_progress = False
                self.call_id = None
                self.call_state = CallState.IDLE
                self.logger.info(f"❌ CALL STATUS: IDLE - 491 Request Pending, call reset")
            else:
                self.logger.info(f"⚠️  491 Request Pending acknowledged - maintaining call state {self.call_state.value}")
            return
        
        if "SIP/2.0 401 Unauthorized" in first_line:
            self._handle_401_unauthorized(message)
        elif "SIP/2.0 200 OK" in first_line:
            self._handle_200_ok(message, headers)
        elif "INVITE" in first_line and "SIP/2.0" in first_line:
            self._handle_incoming_invite(message, headers)
        elif "SIP/2.0 180 Ringing" in first_line:
            self.call_state = CallState.RINGING
            self.logger.info(f"🔔 CALL STATUS: RINGING - Remote party is ringing")
        elif "SIP/2.0 183 Session Progress" in first_line:
            self._handle_session_progress(message, headers)
        elif "ACK" in first_line:
            pass  # ACK received
        elif "BYE" in first_line and "SIP/2.0" in first_line:
            self._handle_bye(message, headers)
        elif "OPTIONS" in first_line and "SIP/2.0" in first_line:
            self._handle_options(message, headers)
        elif "CANCEL" in first_line and "SIP/2.0" in first_line:
            self._handle_cancel(message, headers)
        else:
            pass

    def _send_491_ack(self, response_headers):
        """*** NEW: Send ACK for 491 Response to stop retransmissions ***"""
        cseq_header = response_headers.get('cseq', '')
        if not cseq_header or 'INVITE' not in cseq_header:
            return
            
        to_header = response_headers.get('to', '')
        from_header = response_headers.get('from', '')
        call_id = response_headers.get('call-id', '')
        
        cseq_num = cseq_header.split()[0]
        
        request_uri = f"sip:{self.username}@{self.server}"
        
        branch = self._generate_branch()
        
        msg = f"ACK {request_uri} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: {from_header}\r\n" \
              f"To: {to_header}\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {cseq_num} ACK\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Content-Length: 0\r\n\r\n"
        
        self._send_message(msg)

    def _handle_cancel(self, message, headers):
        """*** NEW: Handle CANCEL request ***"""
        additional_headers = {
            'User-Agent': 'BetterSIPClient/1.0'
        }
        self._send_response(headers, 200, 'OK', additional_headers)
            
    def _handle_options(self, message, headers):
        """Handle OPTIONS request (keepalive)"""
        additional_headers = {
            'Allow': 'INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, MESSAGE, SUBSCRIBE, INFO',
            'Accept': 'application/sdp',
            'User-Agent': 'BetterSIPClient/1.0'
        }
        self._send_response(headers, 200, 'OK', additional_headers)

    def _handle_session_progress(self, message, headers):
        """Handle 183 Session Progress with SDP"""
        if 'body' in headers:
            self._parse_sdp_answer(headers['body'])

    def _handle_200_ok(self, message, headers):
        """*** ENHANCED: 200 OK handling with invite state management ***"""
        call_id = headers.get('call-id', '')
        cseq_header = headers.get('cseq', '')
        self.logger.info(f"✅ 200 OK received - Call-ID: {call_id}, CSeq: {cseq_header}")
        
        if not call_id or not cseq_header:
            return
            
        cseq_method = cseq_header.split()[-1] if cseq_header else ''
        
        if call_id in self.current_transactions:
            transaction = self.current_transactions[call_id]
            
            if transaction['type'] == 'INVITE' and cseq_method == 'INVITE':
                if 'body' in headers:
                    self._parse_sdp_answer(headers['body'])
                
                self.send_ack(headers)
                
                self.invite_in_progress = False
                self.call_state = CallState.CONNECTED
                self.logger.info(f"✅ CALL STATUS: CONNECTED - Call established successfully")
                
                self._send_test_rtp_packet()
                
            elif transaction['type'] == 'REGISTER':
                pass
                
            del self.current_transactions[call_id]
        else:
            self.logger.error(f"❌ No matching transaction found for Call-ID: {call_id}")
            if call_id == self.call_id and cseq_method == 'INVITE':
                self.logger.info(f"🔧 Handling 200 OK without transaction for current call")
                if 'body' in headers:
                    self._parse_sdp_answer(headers['body'])
                
                self.send_ack(headers)
                
                self.invite_in_progress = False
                self.call_state = CallState.CONNECTED
                self.logger.info(f"✅ CALL STATUS: CONNECTED - Call established successfully")
                
                self._send_test_rtp_packet()

    def _handle_incoming_invite(self, message, headers):
        """Enhanced incoming INVITE handling with proper state management"""
        
        call_id = headers.get('call-id', '')
        from_header = headers.get('from', '')
        
        # Extract calling number for logging
        calling_number = "Unknown"
        if from_header and '<sip:' in from_header:
            try:
                calling_number = from_header.split('<sip:')[1].split('@')[0]
            except:
                pass
        
        self.logger.info(f"📞 INCOMING CALL from {calling_number}")
        
        # Set call ID and state
        if call_id:
            self.call_id = call_id
            
        # Parse SDP if present
        if 'body' in headers:
            self.logger.info("🔍 Processing incoming SDP...")
            sdp_parsed = self._parse_sdp_answer(headers['body'])
            if not sdp_parsed:
                self.logger.error("❌ Failed to parse incoming SDP")
                self._send_response(headers, 488, 'Not Acceptable Here')
                return
        
        # Send 180 Ringing
        additional_headers = {
            'User-Agent': 'BetterSIPClient/1.0',
            'Contact': f'<sip:{self.username}@{self.local_ip}:{self.local_port}>'
        }
        self._send_response(headers, 180, 'Ringing', additional_headers)
        self.logger.info("🔔 Sent 180 Ringing")
        
        # Update call state properly
        self.call_state = CallState.RINGING
        
        # Store the invite headers for later use
        self.incoming_invite_headers = headers
        
        # Auto-answer after a short delay
        def auto_answer():
            time.sleep(1)  # Reduced delay
            if self.call_id == call_id and self.running and self.call_state == CallState.RINGING:
                self.logger.info("📞 Auto-answering call...")
                try:
                    self.answer_call(headers)
                    self.call_state = CallState.CONNECTED
                    self.logger.info("✅ CALL STATUS: CONNECTED - Inbound call answered successfully")
                    
                    # Send test RTP packet to establish media
                    self._send_test_rtp_packet()
                    
                except Exception as e:
                    self.logger.error(f"❌ Error answering call: {str(e)}")
                    self.call_state = CallState.IDLE
        
        # Start auto-answer thread
        threading.Thread(target=auto_answer, daemon=True).start()
        
    def _handle_bye(self, message, headers):
        """Enhanced BYE handling with duplicate prevention"""
        
        call_id = headers.get('call-id', '')
        
        # Check if this is a duplicate BYE for the same call
        if hasattr(self, '_last_bye_call_id') and self._last_bye_call_id == call_id:
            self.logger.info(f"🔄 Duplicate BYE ignored for call {call_id}")
            return
        
        self._last_bye_call_id = call_id
        
        # Send 200 OK response
        additional_headers = {
            'User-Agent': 'BetterSIPClient/1.0'
        }
        self._send_response(headers, 200, 'OK', additional_headers)
        self.logger.info(f"📤 Sent 200 OK for BYE")
        
        # Clean up dialog
        if call_id in self.dialogs:
            del self.dialogs[call_id]
        
        # Clean up call state only if this BYE is for the current call
        if call_id == self.call_id:
            self._cleanup_call_state()
            self.logger.info(f"📴 CALL STATUS: IDLE - Call terminated by remote party")
        else:
            self.logger.info(f"📴 BYE processed for different call ID: {call_id}")

    def _cleanup_call_state(self):
        """*** NEW: Clean up call state and invite tracking ***"""
        if self.call_id:
            invite_keys_to_remove = [k for k in self.sent_invites if self.call_id in k]
            for k in invite_keys_to_remove:
                self.sent_invites.discard(k)
        
        self.call_id = None
        self.remote_rtp_info = None
        self.remote_tag = None
        self.invite_in_progress = False
        self.call_state = CallState.IDLE

    def _handle_timeouts(self):
        """*** ENHANCED: Timeout handling with 491 prevention ***"""
        now = datetime.now()
        timed_out = []
        
        for call_id, transaction in list(self.current_transactions.items()):
            elapsed = (now - transaction['start_time']).total_seconds()
            
            retry_intervals = [1.0, 2.0, 4.0]  # Slower retries
            
            if elapsed > 30:  # Shorter final timeout
                timed_out.append(call_id)
                
                if transaction['type'] == 'INVITE':
                    if 'invite_key' in transaction:
                        self.sent_invites.discard(transaction['invite_key'])
                    self.invite_in_progress = False
                continue
            
            for i, interval in enumerate(retry_intervals):
                if elapsed > interval and transaction['retries'] == i:
                    transaction['retries'] = i + 1
                    
                    if transaction['type'] == 'INVITE':
                        if self.call_state in [CallState.RINGING, CallState.CONNECTED, CallState.STREAMING]:
                            continue  # Stop retransmissions once we get ringing
                            
                        if not self.auth_info and not self.invite_in_progress:
                            if 'invite_key' in transaction:
                                self.sent_invites.discard(transaction['invite_key'])
                            self.make_call(transaction['dest_number'])
                        elif self.auth_info:
                            self._retry_invite_with_auth(transaction['dest_number'], call_id)
                    elif transaction['type'] == 'REGISTER':
                        if self.auth_info:
                            self._retry_register_with_auth(call_id)
                        else:
                            self.register()
                    break
        
        for call_id in timed_out:
            del self.current_transactions[call_id]

    def _generate_branch(self):
        """Generate RFC3261 compliant branch ID"""
        return self.branch_prefix + str(random.randint(1000000, 9999999))

    def _send_message(self, message):
        """Enhanced message sending with better error handling"""
        try:
            self.sock.sendto(message.encode(), (self.server, self.port))
        except Exception as e:
            self.logger.error(f"Failed to send SIP message: {str(e)}")
            raise

    def get_local_ip(self):
        """Get local IP address with better detection"""
        if self.local_ip:
            return self.local_ip
            
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self.server, self.port))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
                s.close()
                return ip
            except Exception:
                return '127.0.0.1'

    def hangup_call(self):
        """Enhanced call termination"""
        if not self.call_id:
                return
        
        call_id = self.call_id
        dialog = self.dialogs.get(call_id, {})
        remote_tag = dialog.get('remote_tag')
        remote_uri = dialog.get('remote_uri', f"sip:{self.username}@{self.server}")
        
        branch = self._generate_branch()
        
        to_header = f"<sip:{self.username}@{self.server}>"
        if remote_tag:
            to_header += f";tag={remote_tag}"
        
        msg = f"BYE {remote_uri} SIP/2.0\r\n" \
              f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n" \
              f"Max-Forwards: 70\r\n" \
              f"From: <sip:{self.username}@{self.server}>;tag={self.tag}\r\n" \
              f"To: {to_header}\r\n" \
              f"Call-ID: {call_id}\r\n" \
              f"CSeq: {self.cseq} BYE\r\n" \
              f"User-Agent: BetterSIPClient/1.0\r\n" \
              f"Content-Length: 0\r\n\r\n"
        
        self._send_message(msg)
        self.cseq += 1
        
        if call_id in self.dialogs:
            del self.dialogs[call_id]
        
        self._cleanup_call_state()
        

    def disconnect(self):
        """Enhanced cleanup and disconnect"""
        self.running = False
        
        if self.call_id:
            self.hangup_call()
        
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        if self.rtp_sock:
            try:
                self.rtp_sock.close()
            except:
                pass
        
        self.sent_invites.clear()
        self.current_transactions.clear()
        self.dialogs.clear()
        

    def send_dtmf(self, digit):
        """Send DTMF tone via RTP"""
        if not self.remote_rtp_info:
                return
        
        dtmf_map = {'0': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, 
                   '*': 10, '#': 11, 'A': 12, 'B': 13, 'C': 14, 'D': 15}
        
        if digit not in dtmf_map:
            return
        
        event = dtmf_map[digit]
        
        payload = struct.pack('!BBHH', event, 0x0A, 160, 0)
        header = struct.pack('!BBHII', 0x80, 101, self.rtp_seq, self.rtp_timestamp, self.rtp_ssrc)
        
        try:
            self.rtp_sock.sendto(header + payload, self.remote_rtp_info)
            self.rtp_seq = (self.rtp_seq + 1) % 65536
            time.sleep(0.1)
            
            payload = struct.pack('!BBHH', event, 0x8A, 800, 0)  # End event
            header = struct.pack('!BBHII', 0x80, 101, self.rtp_seq, self.rtp_timestamp, self.rtp_ssrc)
            self.rtp_sock.sendto(header + payload, self.remote_rtp_info)
            self.rtp_seq = (self.rtp_seq + 1) % 65536
            
        except Exception as e:
            self.logger.error(f"Error sending DTMF: {str(e)}")

    def get_call_status(self):
        """Get current call status with detailed information"""
        return {
            'state': self.call_state.value,
            'call_id': self.call_id,
            'remote_rtp': self.remote_rtp_info,
            'local_rtp_port': self.local_rtp_port,
            'active_transactions': len(self.current_transactions),
            'dialogs': len(self.dialogs),
            'sent_invites': len(self.sent_invites),
            'invite_in_progress': self.invite_in_progress,
            'auth_available': bool(self.auth_info),
            'audio_buffer_size': len(self.audio_buffer)
        }
        
    def print_call_status(self):
        """Print current call status in a readable format"""
        status = self.get_call_status()
        state_emoji = {
            'idle': '📴',
            'inviting': '📞', 
            'ringing': '🔔',
            'connected': '✅',
            'streaming': '🎙️'
        }
        
        emoji = state_emoji.get(status['state'], '🔵')
        self.logger.info(f"{emoji} CALL STATUS: {status['state'].upper()}")
        
        if status['call_id']:
            self.logger.info(f"  Call ID: {status['call_id']}")
        if status['remote_rtp']:
            self.logger.info(f"  Remote RTP: {status['remote_rtp']}")
        if status['local_rtp_port']:
            self.logger.info(f"  Local RTP: {self.local_ip}:{status['local_rtp_port']}")
        if status['audio_buffer_size'] > 0:
            self.logger.info(f"  Audio buffer: {status['audio_buffer_size']} packets")
