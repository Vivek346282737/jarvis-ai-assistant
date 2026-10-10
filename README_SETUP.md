# JARVIS - apna setup (naye user ke liye)

Ye JARVIS ki ek saaf copy hai. Isme kisi aur ka koi data nahi hai. Jo bhi aap daaloge (keys, phone, awaaz, contacts) wo
sirf isi folder me, aapke PC pe rahega.

## Chahiye
- Windows 10/11, microphone, internet
- Android phone (optional), PC aur phone ek hi Wi-Fi pe, aur ek USB data cable
- Kam se kam ek AI key: Groq (console.groq.com, free) ya Gemini (aistudio.google.com)

Python pehle se install karne ki zaroorat **nahi** hai; installer khud kar deta hai.

## Install (ek baar)
1. Folder ko kahin bhi rakho, jaise `C:\JARVIS`.
2. **`Install_JARVIS.bat` pe double-click karo.** Bas.
   (Ya PowerShell se: `powershell -ExecutionPolicy Bypass -File .\Install_JARVIS.ps1`)

   Installer har kadam pe batata hai kya ho raha hai:
   - `[1/5]` Python 3.11 - hai to wahi; nahi hai to python.org se official installer download karke khud install karta hai
     (file ka signature jaanch ke, sirf aapke Windows user ke liye, admin nahi chahiye)
   - `[2/5]` is folder ka apna Python environment
   - `[3/5]` saari libraries (5-15 minute, internet chahiye)
   - `[4/5]` JARVIS ki jaanch
   - `[5/5]` first-time setup

   Agar kuch apne aap na ho paye (internet band, antivirus), to installer ruk ke saaf likhta hai ki haath se kya karna hai.
   Theek karke dobara double-click karo; jo ho chuka hai wo dobara nahi hota.
3. **Optional - free local AI (Ollama):** bina cloud key ke bhi chalne ke liye. Ye bada download hai (installer ~1.5 GB +
   models 5-8 GB), isliye apne aap nahi hota. Chahiye to:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Install_JARVIS.ps1 -WithOllama -SkipSetup
   ```
4. Installer khud first-time setup shuru karega. Wo poochega:
   - aapka naam (JARVIS aapko isi naam se jaanega)
   - AI keys
   - phone ka `IP:PORT`
   - phone ka PIN/password (sirf tab use hota hai jab aap "unlock phone" bolte ho)
   - aapki awaaz (8 chhote sentence)

   Koi bhi step skip kar sakte ho aur baad me chala sakte ho.

## Phone jodna (IP type nahi karna padta)
Pehle phone me ek baar: Settings > About phone > "Build number" pe 7 baar tap, phir Settings > Developer options.

**Sabse aasaan aur pakka tareeka: USB (data) cable, sirf ek baar**

1. Developer options me **USB debugging** on karo.
2. Phone ko **data cable** se PC me lagao (sirf charging wali cable se nahi hoga).
3. Phone pe ek box aayega: **"Allow USB debugging?"**
   - Pehle **"Always allow from this computer"** wala checkbox **TICK karo**.
   - Phir **Allow** dabao.

   Ye tick sabse zaroori hai. Tick kiya to phone is PC ko hamesha yaad rakhta hai aur dobara kabhi nahi poochta.
   Tick nahi kiya to phone har baar poochega.
4. Chalao (setup ye khud bhi karta hai):
   ```powershell
   .\venv\Scripts\python.exe -m jarvis_addons.phone_link
   ```
   JARVIS phone dhoondhta hai, brand/model dikha ke poochta hai "ye aapka phone hai?", aur phone ko Wi-Fi pe le aata hai.
   Wo ye bhi batata hai ki "Always allow" save hua ya nahi.
5. **Ab cable nikaal do.** Aage se sab Wi-Fi pe, apne aap. IP kabhi type nahi karna.

**Teen baatein jo pehle se jaan lo**

- **Phone restart (switch off/on) hone par:** Android Wi-Fi debugging khud band kar deta hai (ye Android ka niyam hai).
  Tab bas cable ek baar kuch second ke liye lagao. Na kuch type karna hai, na "Allow" dabana hai - JARVIS chal raha ho to
  wo phone ko khud Wi-Fi pe wapas le aata hai, phir cable nikaal do.
- **7 din ka niyam:** naye Android phone us PC ko bhool jaate hain jo 7 din tak na juda ho. Setup poochta hai
  "Turn that expiry off?" - **Y** bol do, to phone kabhi nahi bhoolega. (Haath se: Developer options >
  "Disable adb authorization timeout" on.) Roz JARVIS chalate ho to ye waise bhi nahi lagta.
- **Kabhi bhi jaanchna ho** ki phone is PC ko pakka maanta hai ya nahi:
  ```powershell
  .\venv\Scripts\python.exe -m jarvis_addons.phone_link --trust
  ```

Agar galti se tick ke bina Allow daba diya: cable nikaal ke dobara lagao, box phir aayega, is baar tick karke Allow dabao.

**Bina cable ke:** Developer options > **Wireless debugging** on karo, use kholo, "Pair device with pairing code" dabao aur
wo screen khuli rakho. Phir wahi command chalao; JARVIS sirf phone pe dikha 6-digit code poochega.

Phone aur PC ek hi Wi-Fi pe hone chahiye. Kuch na mile to wahi command aakhir me IP:PORT haath se likhne deta hai.
Sirf dekhna ho ki kya mil raha hai: `.\venv\Scripts\python.exe -m jarvis_addons.phone_link --find`

## Phone ka lock
- PIN ya password: setup me daal do (ya baad me `live_cmd_console.py --set-phone-password VALUE`).
- **Pattern** lock: dots ko keypad ki tarah gino (`1 2 3 / 4 5 6 / 7 8 9`) aur apna pattern number ki tarah do:
  ```powershell
  .\venv\Scripts\python.exe -m jarvis_addons.pattern_lock --set 14789
  .\venv\Scripts\python.exe -m jarvis_addons.pattern_lock --gesture-test    # phone seedha rakh ke: batata hai gesture aapke phone pe chalta hai ya nahi
  .\venv\Scripts\python.exe -m jarvis_addons.pattern_lock --status
  ```
  Pattern unlock ke liye Android 11 ya naya chahiye. JARVIS ek command me sirf ek baar koshish karta hai, aur 2 baar fail hone par
  ruk jaata hai taaki phone "too many attempts" me na jaaye.
- Phone ulta rakha ho ya jeb me ho to screen nahi jalti aur unlock nahi hoga; JARVIS ye bata dega.
- Phone ki jaanch: `.\venv\Scripts\python.exe -m jarvis_addons.phone_compat`

## Chalana
```powershell
.\JARVIS_Launcher.ps1 -Console
```

## Baad me kuch badalna ho
```powershell
.\venv\Scripts\python.exe jarvis_setup.py                              # baaki steps dobara
.\venv\Scripts\python.exe live_cmd_console.py --setup-groq             # Groq keys
.\venv\Scripts\python.exe live_cmd_console.py --set-target IP:PORT     # phone
.\venv\Scripts\python.exe live_cmd_console.py --spk-enroll             # awaaz dobara sikhana
.\venv\Scripts\python.exe live_cmd_console.py --say "what time is it"  # bina mic ke ek command
```

## Usage log (aapko pata hona chahiye)
JARVIS ek chhoti local file `usage_log.jsonl` rakhta hai taaki developer bugs theek kar sake. Isme sirf ye hota hai:
kaunsa feature chala, chala ya fail hua, error ka type, aur command ka "dhaancha" (jaise `send message to _ _ _`).

Isme **kabhi nahi** hota: aapke shabd, naam, number, message, file ka path, keys ya password.
JARVIS ye file khud kahin nahi bhejta.

```powershell
.\venv\Scripts\python.exe -m jarvis_addons.usage_log          # dekho kya-kya record hua
.\venv\Scripts\python.exe -m jarvis_addons.usage_log --off    # band karo (--on se dobara chalu)
```

## Aapka data
- Sab kuch `jarvis_memory.db` me, isi folder me rehta hai. Ye file kisi ko mat bhejna: isme aapki keys, contacts aur messages hote hain.
- Pehli baar WhatsApp message asli me nahi bhejta ("dry run" on hota hai). Bhejna shuru karne ke liye bolo: "dry run off".
