"""The init script every browser context runs, hardened against inspection.

No canvas / WebGL / audio fingerprint spoofing, and no playwright-stealth.
Those patches are detectable in themselves: overriding those prototypes
reads as a privacy extension, and Facebook responds by never finishing its
render (an infinite spinner), Twitter with "Something went wrong", and
Instagram by not hydrating at all.

Instead, this script applies strictly necessary defenses with native function
masking so property descriptor audits (`toString()` checks) return standard
`[native code]` signatures, polyfills desktop `window.chrome`, stabilizes
headless notification permissions, locks hardware stats to realistic counts,
scrubs CDP runtime variables, masks WebRTC local host IPs, and synthesizes
realistic media devices and power/network telemetry.
"""

from __future__ import annotations


def build_init_js(hardware_concurrency: int = 8, device_memory: int = 8) -> str:
    """Builds a robust initialization script with native function masking and multi-tier shielding."""
    return f"""
(() => {{
    try {{
        const maskFunction = (fn, name) => {{
            const rep = `function get ${{name}}() {{ [native code] }}`;
            return new Proxy(fn, {{
                get: (target, prop) => prop === 'toString' ? () => rep : target[prop]
            }});
        }};

        try {{
            delete Object.getPrototypeOf(navigator).webdriver;
            delete navigator.webdriver;
        }} catch (e) {{}}

        Object.defineProperty(navigator, 'webdriver', {{
            get: maskFunction(() => undefined, 'webdriver'),
            configurable: true
        }});

        Object.defineProperty(document, 'visibilityState', {{
            get: maskFunction(() => 'visible', 'visibilityState'),
            configurable: true
        }});

        Object.defineProperty(document, 'hidden', {{
            get: maskFunction(() => false, 'hidden'),
            configurable: true
        }});

        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: maskFunction(() => {hardware_concurrency}, 'hardwareConcurrency'),
            configurable: true
        }});

        Object.defineProperty(navigator, 'deviceMemory', {{
            get: maskFunction(() => {device_memory}, 'deviceMemory'),
            configurable: true
        }});

        if (!window.chrome || !window.chrome.runtime) {{
            window.chrome = window.chrome || {{}};
            window.chrome.runtime = window.chrome.runtime || {{}};
            window.chrome.app = window.chrome.app || {{
                isInstalled: false,
                InstallState: {{
                    DISABLED: 'disabled',
                    INSTALLED: 'installed',
                    NOT_INSTALLED: 'not_installed'
                }},
                getDetails: maskFunction(() => null, 'getDetails'),
                getIsInstalled: maskFunction(() => false, 'getIsInstalled'),
                runningState: maskFunction(() => 'cannot_run', 'runningState')
            }};
            window.chrome.csi = window.chrome.csi || maskFunction(() => ({{}}), 'csi');
            window.chrome.loadTimes = window.chrome.loadTimes || maskFunction(() => ({{}}), 'loadTimes');
        }}

        if (window.navigator && window.navigator.permissions) {{
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = function query(parameters) {{
                if (parameters && parameters.name === 'notifications') {{
                    return Promise.resolve({{ state: Notification.permission, onchange: null }});
                }}
                return originalQuery.apply(this, arguments);
            }};
        }}

        // --- 1. CDP & Playwright Variable Scrubbing ---
        try {{
            const blockRegex = /^(cdc_|__playwright|__puppeteer|__webdriver|\\$chrome)/i;
            const cleanProps = (props) => props.filter(p => !blockRegex.test(p));
            const origGetOwnPropertyNames = Object.getOwnPropertyNames;
            Object.defineProperty(Object, 'getOwnPropertyNames', {{
                value: maskFunction(function(obj) {{
                    const result = origGetOwnPropertyNames(obj);
                    if (obj === window || obj === document) {{
                        return cleanProps(result);
                    }}
                    return result;
                }}, 'getOwnPropertyNames'),
                configurable: true,
                writable: true
            }});
            const origKeys = Object.keys;
            Object.defineProperty(Object, 'keys', {{
                value: maskFunction(function(obj) {{
                    const result = origKeys(obj);
                    if (obj === window || obj === document) {{
                        return cleanProps(result);
                    }}
                    return result;
                }}, 'keys'),
                configurable: true,
                writable: true
            }});
            for (const prop of origGetOwnPropertyNames(window)) {{
                if (blockRegex.test(prop)) {{
                    try {{ delete window[prop]; }} catch (e) {{}}
                }}
            }}
        }} catch (e) {{}}

        // --- 2. Mocking Media Devices & Speech Synthesis ---
        try {{
            if (navigator.mediaDevices) {{
                const origEnumerate = navigator.mediaDevices.enumerateDevices;
                navigator.mediaDevices.enumerateDevices = maskFunction(() => {{
                    return origEnumerate.call(navigator.mediaDevices).then(devices => {{
                        if (devices && devices.length > 0) return devices;
                        return [
                            {{ deviceId: 'default', kind: 'audiooutput', label: 'Default Audio Output', groupId: 'default_audio_group' }},
                            {{ deviceId: 'internal_mic', kind: 'audioinput', label: 'Internal Microphone', groupId: 'default_audio_group' }},
                            {{ deviceId: 'internal_webcam', kind: 'videoinput', label: 'Integrated Camera', groupId: 'default_video_group' }}
                        ];
                    }});
                }}, 'enumerateDevices');
            }}
            if (window.speechSynthesis && typeof window.speechSynthesis.getVoices === 'function') {{
                const origGetVoices = window.speechSynthesis.getVoices;
                window.speechSynthesis.getVoices = maskFunction(function() {{
                    const voices = origGetVoices.apply(this, arguments);
                    if (voices && voices.length > 0) return voices;
                    return [
                        {{ default: true, lang: 'en-US', localService: true, name: 'Microsoft David Desktop - English (United States)', voiceURI: 'Microsoft David Desktop - English (United States)' }},
                        {{ default: false, lang: 'en-US', localService: true, name: 'Microsoft Zira Desktop - English (United States)', voiceURI: 'Microsoft Zira Desktop - English (United States)' }}
                    ];
                }}, 'getVoices');
            }}
        }} catch (e) {{}}

        // --- 3. WebRTC Local STUN/ICE Candidate Masking ---
        try {{
            const wrapRTC = (RTCConstructor) => {{
                if (!RTCConstructor) return undefined;
                return new Proxy(RTCConstructor, {{
                    construct: (target, args) => {{
                        const pc = new target(...args);
                        const origAddIceCandidate = pc.addIceCandidate;
                        if (origAddIceCandidate) {{
                            pc.addIceCandidate = maskFunction(function(candidate) {{
                                if (candidate && candidate.candidate && /((192\\.168\\.)|(10\\.)|(172\\.1[6-9]\\.)|(172\\.2[0-9]\\.)|(172\\.3[0-1]\\.))/.test(candidate.candidate)) {{
                                    return Promise.resolve();
                                }}
                                return origAddIceCandidate.apply(this, arguments);
                            }}, 'addIceCandidate');
                        }}
                        return pc;
                    }}
                }});
            }};
            if (window.RTCPeerConnection) window.RTCPeerConnection = wrapRTC(window.RTCPeerConnection);
            if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = wrapRTC(window.webkitRTCPeerConnection);
            if (window.mozRTCPeerConnection) window.mozRTCPeerConnection = wrapRTC(window.mozRTCPeerConnection);
        }} catch (e) {{}}

        // --- 4. Battery & Broadband Network Information APIs ---
        try {{
            if (!navigator.connection) {{
                Object.defineProperty(navigator, 'connection', {{
                    get: maskFunction(() => ({{
                        effectiveType: '4g',
                        rtt: 50,
                        downlink: 10,
                        saveData: false,
                        onchange: null
                    }}), 'connection'),
                    configurable: true
                }});
            }}
            if (!navigator.getBattery) {{
                Object.defineProperty(navigator, 'getBattery', {{
                    value: maskFunction(() => Promise.resolve({{
                        charging: true,
                        chargingTime: 0,
                        dischargingTime: Infinity,
                        level: 1.0,
                        onchargingchange: null,
                        onchargingtimechange: null,
                        ondischargingtimechange: null,
                        onlevelchange: null
                    }}), 'getBattery'),
                    configurable: true,
                    writable: true
                }});
            }}
        }} catch (e) {{}}

    }} catch (err) {{
        // Swallow unhandled setup errors to ensure page rendering never fails
    }}
}})();
"""


INIT_JS = build_init_js(8, 8)
