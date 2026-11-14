# Email to counsel: H.264 patents and the FFmpeg (LGPL) build

Status: a draft for Justin to send. It asks questions; it is not legal advice. Facts checked against the
repository on 2026-11-09; production status corrected on 2026-11-13 (`docs/PROGRESS.md:117-118`). Windows release
status added on 2026-11-13: DoMe for Windows 0.1.0, with Screen view and the FFmpeg build inside, was published on
GitHub that day as a free beta, by Justin's decision before counsel answers [GitHub Actions Release run
43513385142, 2026-11-13] (`docs/LAUNCH_SETUP.md:868-889`; `docs/PROGRESS.md:31-38`;
`docs/legal/founder-decisions.md:49-57`); Screen view stays switched off for accounts on domeremote.com
(`docs/PROGRESS.md:31-38`; [GitHub Actions Deploy run 43508425555, 2026-11-13]). The FFmpeg build itself has been
public longer: since 2026-10-22, inside a PyAV wheel published as the GitHub pre-release
`av-lgpl-17.1.0-ffmpeg-n8.1.3-r3` (`docs/WINDOWS_INSTALL.md:545-547`; [GitHub release
av-lgpl-17.1.0-ffmpeg-n8.1.3-r3, published 2026-10-22, pre-release]).

## For Justin only (do not send this part)

Send the email below to a US technology or IP lawyer before Screen view is switched on for customers: their
answer is one of the launch conditions. Attach nothing secret: no passwords, keys, tokens, account pages or
code. If you like, attach `THIRD_PARTY_NOTICES.txt`, the list of outside software and licences that comes with
DoMe (on your PC, paste `%LOCALAPPDATA%\Programs\DoMe` into File Explorer's address bar), and the licence terms
Setup shows (an engineer can send you that file, `licence-terms.txt`); both are drafts the lawyer is asked to
check. What to expect: a short paid consult, usually one call plus a written reply. A lawyer normally runs a
quick conflict check and sends an engagement letter first, and may ask follow-up questions; an engineer can
answer the technical ones. Ask for the fee before you agree, and ask for the answer in writing so it can be
kept with the launch records.

## The email

Subject: Short consult before launch: H.264 patent licensing and FFmpeg (LGPL) in my Windows app

Hi [name],

I'm Justin Gaskins, a sole founder in New Jersey. Before I start selling DoMe, I'd like a short paid consult
on H.264 patent licensing and on the LGPL rules for the FFmpeg libraries I ship.

The product. A Windows program plus a phone web app for controlling your own PC from your iPhone, sold by
subscription: Free, Pro ($9.99 a month) and Pro Plus ($24.99 a month); the paid plans are not on sale yet.
It is new. My website and server have been live at domeremote.com since October 2026, with free accounts only.
On November 13, 2026, before hearing from you, I published the Windows program (version 0.1.0) for download from
GitHub as a free beta. A beta feature, Screen view, shows the PC's screen live on the customer's own phone. Its
code, including the FFmpeg build described below, is inside that installer, but Screen view is not switched on
for customers until I hear from you.

Who encodes and decodes the video.
- Encoding: Windows, on the customer's PC. DoMe asks Microsoft's Media Foundation, through FFmpeg, for H.264:
  from the graphics card's encoder if Windows offers one, otherwise Windows' own software encoder.
- Decoding: Apple, on the customer's iPhone: Safari's built-in WebRTC and Apple's decoder. My web app
  contains no decoder.
- Fallback: VP8 (Google's libvpx, royalty-free under Google's patent grant).
- Path: directly between the two devices, encrypted; otherwise through Cloudflare's relay, which carries
  encrypted packets it cannot read. My own server never carries video.

What I distribute. Since November 13, 2026, publicly on GitHub: a Windows installer with my own LGPL-2.1-or-later
build of FFmpeg 8.1.3. The same FFmpeg build has been publicly downloadable from my GitHub repository since
October 22, 2026, as a pre-release for developers: a package of the Python library PyAV that contains the FFmpeg
DLLs. The build is configured --disable-gpl --disable-nonfree --disable-version3 --disable-everything
--disable-network, enabling only the encoders h264_mf, libvpx_vp8 and libopus and one decoder, wrapped_avframe
(it unwraps raw frames made on the same PC): no parsers, bitstream filters or hardware decoders. So its only
H.264-related part is h264_mf, FFmpeg's wrapper that hands frames to Media Foundation and returns the result. No
x264, OpenH264 or other H.264 encoder or decoder is included; a build check refuses any build that has one. My
code and an open-source WebRTC library (aiortc) handle only the packaging of the encoder's output, never the
pictures: they re-frame it where needed (length prefixes to start codes, for the encoder's setup data too),
split it into network packets, repeat that setup data before key frames, and read a few header fields (to find
key frames, and the profile and level for diagnostics). Nothing in DoMe decodes or re-encodes the H.264 video.

My LGPL steps (drafts). The FFmpeg DLLs are separate files in the install folder, loaded at run time, that a
user may replace. The installer ships the LGPL text and a notices file with a written offer, good for at
least three years, of the libraries' source and build scripts at cost. The licence terms shown in Setup
allow modifying DoMe for one's own use and reverse engineering to debug that. (pystray, a tray-icon library,
is LGPL-3 and ships as replaceable source files.)

My questions:
1. Do I need an AVC/H.264 patent licence, for example from Via Licensing Alliance (formerly MPEG LA), to
   distribute this installer, when the encoder is Windows' or the graphics driver's and the decoder is
   Apple's? I found only an informal 2010 Microsoft statement that apps calling its codec are covered.
2. Does the h264_mf wrapper in my FFmpeg build, or the packet handling above, change that answer?
3. Does running the service (my server, plus Cloudflare relaying encrypted video) need a licence of its own?
4. If a licence is needed, which one, and what is involved? My notes say Via LA's AVC terms are $0 for the
   first 100,000 units a year. Is that my category?
5. Are my LGPL steps enough? Is a written offer sufficient, or should I post the source and build scripts
   next to the download? I can send you the draft notices and licence terms.
6. If the H.264 answer is no: VP8 is already built as the fallback, and my engineer would add a switch to make
   it the only option. Would anything else need to change?

A short written answer I can keep on file would be ideal. Could you tell me your fee up front?

Thanks,
Justin Gaskins
Founder, DoMe
New Jersey
