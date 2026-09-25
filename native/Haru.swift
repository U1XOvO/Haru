import AppKit
import WebKit
import AVFoundation
import UniformTypeIdentifiers
import Darwin
#if HARU_RELEASE
import Sparkle
#endif

final class HaruApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var root: URL!
    var python: String = ""
    var dataDir: URL!
    var storageRoot: URL!
    var bundledBackend = false
    var distribution = "source"
    var updatesEnabled = false
    var instanceFD: Int32 = -1
    var maintenanceMode = false
    var smokeReport: URL? = {
        let args=CommandLine.arguments
        return args.count == 3 && args[1] == "--smoke-test" ? URL(fileURLWithPath:args[2]) : nil
    }()
    var maintenanceCompletions: [Int: ([String:Any])->Void] = [:]
    #if HARU_RELEASE
    var updaterController: SPUStandardUpdaterController?
    var pendingInstall: (() -> Void)?
    #endif
    var speechGeneration = UUID()
    var speechProcess: Process?
    var speechReplyID: Int?
    var recorder: AVAudioRecorder?
    var player: AVAudioPlayer?
    var audioPaused = false
    var recordingTimer: Timer?
    let processLock = NSLock()
    var backendProcesses: [Int32: Process] = [:]
    var shuttingDown = false
    let queue = DispatchQueue(label: "haru.backend", attributes: .concurrent)
    let backendDeadlineSeconds: TimeInterval = 600

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let iconURL = Bundle.main.url(forResource: "Haru", withExtension: "icns"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
        guard let resource = Bundle.main.resourceURL else { fatalAlert("应用资源不完整，请重新安装。"); return }
        if let data = try? Data(contentsOf: resource.appendingPathComponent("app-release.json")),
           let config = try? JSONSerialization.jsonObject(with: data) as? [String:Any] {
            bundledBackend = true
            root = resource
            python = resource.appendingPathComponent("backend/HaruBackend").path
            distribution = config["distribution"] as? String ?? "preview"
            updatesEnabled = config["updates_enabled"] as? Bool ?? false
            storageRoot = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Haru", isDirectory:true)
        } else {
            guard let configData = try? Data(contentsOf: resource.appendingPathComponent("runtime.json")),
                  let config = try? JSONSerialization.jsonObject(with: configData) as? [String:String],
                  let rootPath = config["root"], let pythonPath = config["python"] else {
                fatalAlert("应用运行配置无效，请重新运行 start.command 或重新安装 Haru。"); return
            }
            root = URL(fileURLWithPath:rootPath, isDirectory:true)
            python = pythonPath
            storageRoot = root
        }
        if let override = ProcessInfo.processInfo.environment["HARU_STORAGE_DIR"] {
            storageRoot = URL(fileURLWithPath:override, isDirectory:true)
        }
        dataDir = URL(fileURLWithPath: ProcessInfo.processInfo.environment["HARU_DATA_DIR"] ?? storageRoot.appendingPathComponent("runtime").path, isDirectory:true)
        guard FileManager.default.isExecutableFile(atPath:python) else {fatalAlert("找不到 Haru 运行组件，请重新构建或重新安装。");return}
        do { try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories:true) }
        catch { fatalAlert("无法打开学习数据目录，请检查权限。"); return }
        instanceFD = open(storageRoot.appendingPathComponent(".app.lock").path, O_CREAT | O_RDWR, 0o600)
        guard instanceFD >= 0, flock(instanceFD, LOCK_EX | LOCK_NB) == 0 else {fatalAlert("Haru 已在运行，请使用已打开的窗口。");return}
        let menu = NSMenu(); let appMenu = NSMenu(); let appItem = NSMenuItem()
        appItem.submenu = appMenu; menu.addItem(appItem)
        appMenu.addItem(withTitle: "关于 Haru", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(withTitle:"检查更新…", action:#selector(checkUpdates), keyEquivalent:"")
        appMenu.addItem(.separator()); appMenu.addItem(withTitle: "退出 Haru", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = NSMenu(title: "编辑"); let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: ""); editItem.submenu = edit; menu.addItem(editItem)
        for (name, action, key) in [("撤销", "undo:", "z"), ("重做", "redo:", "Z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] { edit.addItem(withTitle:name, action:Selector(action), keyEquivalent:key) }
        NSApp.mainMenu = menu
        let configView = WKWebViewConfiguration()
        if smokeReport != nil {
            configView.userContentController.addUserScript(WKUserScript(source:"window.haruSmokeMode=true;",injectionTime:.atDocumentStart,forMainFrameOnly:true))
            DispatchQueue.main.asyncAfter(deadline:.now()+45){if self.smokeReport != nil {self.finishSmoke(false)}}
        }
        configView.userContentController.add(self, name: "haru")
        configView.preferences.javaScriptCanOpenWindowsAutomatically = false
        web = WKWebView(frame:.zero,configuration:configView)
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect:NSRect(x:0,y:0,width:1280,height:900),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title = "Haru · 日语，在日常里"
        window.minSize = NSSize(width:840,height:660)
        window.backgroundColor = NSColor(calibratedRed:0.97,green:0.98,blue:0.99,alpha:1)
        window.contentView = web
        window.center(); window.makeKeyAndOrderFront(nil)
        let index = root.appendingPathComponent("ui/index.html")
        web.loadFileURL(index,allowingReadAccessTo:root.appendingPathComponent("ui",isDirectory:true))
        NSApp.activate(ignoringOtherApps:true)
        #if HARU_RELEASE
        if updatesEnabled {
            updaterController = SPUStandardUpdaterController(startingUpdater:true, updaterDelegate:self, userDriverDelegate:nil)
        }
        #endif
    }
    func fatalAlert(_ message:String) {let a=NSAlert(); a.messageText="Haru 暂时无法启动"; a.informativeText=message; a.runModal(); NSApp.terminate(nil)}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
    func applicationWillTerminate(_ notification: Notification) {
        recorder?.stop(); stopAudio()
        processLock.lock()
        shuttingDown = true
        let children = Array(backendProcesses.values)
        for process in children where process.isRunning { process.terminate() }
        processLock.unlock()
        // Give Python a short chance to close SQLite and its pipes, then make
        // app shutdown deterministic even if a child ignores SIGTERM.
        let graceDeadline = Date().addingTimeInterval(1.0)
        while Date() < graceDeadline && children.contains(where: { $0.isRunning }) {
            Thread.sleep(forTimeInterval: 0.025)
        }
        for process in children where process.isRunning {
            _ = kill(process.processIdentifier, SIGKILL)
        }
    }
    func reply(_ id:Int,_ result:[String:Any]) {
        if let completion = maintenanceCompletions.removeValue(forKey:id) {completion(result);return}
        guard let data=try? JSONSerialization.data(withJSONObject:result,options:[.fragmentsAllowed]),let json=String(data:data,encoding:.utf8) else{return}
        web.evaluateJavaScript("window.haruResolve(\(id),\(json))",completionHandler:nil)
    }
    func ok(_ id:Int,_ data:Any = [String:String]()) {reply(id,["ok":true,"data":data])}
    func fail(_ id:Int,_ error:String) {reply(id,["ok":false,"error":error])}
    func registerBackendProcess(_ process: Process, speechToken: UUID? = nil) -> Bool {
        processLock.lock(); defer { processLock.unlock() }
        guard !shuttingDown else { return false }
        if let token = speechToken {
            guard token == speechGeneration else { return false }
            speechProcess = process
        }
        backendProcesses[process.processIdentifier] = process
        return true
    }
    func unregisterBackendProcess(_ process: Process) {
        processLock.lock()
        backendProcesses.removeValue(forKey: process.processIdentifier)
        if speechProcess === process { speechProcess = nil }
        processLock.unlock()
    }
    func stopBackendProcess(_ process: Process, grace: TimeInterval = 1.0) {
        guard process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(grace)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.025) }
        if process.isRunning { _ = kill(process.processIdentifier, SIGKILL) }
    }
    func isCurrentSpeech(_ token: UUID) -> Bool {
        processLock.lock(); defer { processLock.unlock() }
        return !shuttingDown && speechGeneration == token
    }
    // Called on the main thread. Stop never waits for the synthesis network request.
    func stopAudio() {
        processLock.lock()
        speechGeneration = UUID()
        let process = speechProcess
        speechProcess = nil
        processLock.unlock()
        player?.stop(); player = nil
        audioPaused = false
        if let id = speechReplyID { speechReplyID = nil; ok(id) }
        if let process { queue.async { self.stopBackendProcess(process) } }
    }
    func toggleAudioPause(_ id:Int) {
        guard let player else { audioPaused = false; ok(id,["state":"idle"]); return }
        if audioPaused {
            guard player.play() else {fail(id,"无法继续播放，请检查输出设备。");return}
            audioPaused = false
            ok(id,["state":"playing"])
        } else if player.isPlaying {
            player.pause()
            audioPaused = true
            ok(id,["state":"paused"])
        } else {
            audioPaused = false
            ok(id,["state":"idle"])
        }
    }
    func audioState() -> String {
        if audioPaused { return "paused" }
        return player?.isPlaying == true ? "playing" : "idle"
    }
    func speechFile(_ data: [String:Any]) -> URL? {
        let engine = data["engine"] as? String ?? "edge"
        guard engine == "edge" || engine == "gemini", let name = data["audio"] as? String else { return nil }
        let fileType = engine == "gemini" ? "wav" : "mp3"
        guard name.range(of:"^[a-f0-9]{32}\\.\(fileType)$", options:.regularExpression) != nil else { return nil }
        let folderName = engine == "gemini" ? "tts-gemini-cache" : "tts-cache"
        let folder = dataDir.appendingPathComponent(folderName, isDirectory:true).resolvingSymlinksInPath()
        let file = folder.appendingPathComponent(name)
        guard let info = try? file.resourceValues(forKeys:[.isRegularFileKey, .isSymbolicLinkKey]),
              info.isRegularFile == true, info.isSymbolicLink != true,
              file.resolvingSymlinksInPath().deletingLastPathComponent().path == folder.path else { return nil }
        return file
    }
    func startSpeech(_ id: Int, _ params: [String:Any]) {
        guard let text = params["text"] as? String, !text.isEmpty, text.unicodeScalars.count <= 6000 else {
            fail(id,"朗读文本无效。"); return
        }
        stopAudio()
        processLock.lock(); let token = speechGeneration; processLock.unlock()
        speechReplyID = id
        runBackend(id,"speech_prepare",params,speechToken:token) { result in
            let data = result["data"] as? [String:Any] ?? [:]
            guard self.isCurrentSpeech(token) else { return }
            self.speechReplyID = nil
            guard result["ok"] as? Bool == true else {
                self.fail(id,result["error"] as? String ?? "日语语音生成失败，请重试。"); return
            }
            if data["cancelled"] as? Bool == true { self.ok(id); return }
            guard let audio = result["_speechAudio"] as? Data else { self.fail(id,"朗读音频不可用，请重试。"); return }
            do {
                let player = try AVAudioPlayer(data:audio)
                guard player.play() else { self.fail(id,"音频无法播放，请检查输出设备。"); return }
                self.player = player
                self.ok(id,["engine":data["engine"] as? String ?? "edge",
                            "fallback":data["fallback"] as? String ?? ""])
            } catch { self.fail(id,"音频无法播放，请检查输出设备后重试。") }
        }
    }
    func userContentController(_ userContentController: WKUserContentController,didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.isFileURL == true,
              message.frameInfo.request.url?.standardizedFileURL.path == root.appendingPathComponent("ui/index.html").standardizedFileURL.path,
              let m=message.body as? [String:Any],let id=m["id"] as? Int,let action=m["action"] as? String else{return}
        if maintenanceMode {fail(id,"正在准备更新或迁移，请重新打开 Haru。");return}
        let p=m["params"] as? [String:Any] ?? [:]
        switch action {
        case "smoke_result":
            guard smokeReport != nil else {fail(id,"操作无效。");return}
            finishSmoke(p["ok"] as? Bool == true)
        case "app_info":
            var automatic = false
            #if HARU_RELEASE
            automatic = updaterController?.updater.automaticallyChecksForUpdates ?? false
            #endif
            ok(id,["version":Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "unknown", "distribution":distribution, "updates_enabled":updatesEnabled, "automatic_updates":automatic])
        case "check_updates":
            checkUpdates();ok(id)
        case "update_preferences":
            guard let enabled=p["enabled"] as? Bool else {fail(id,"更新设置无效。");return}
            #if HARU_RELEASE
            updaterController?.updater.automaticallyChecksForUpdates = enabled
            #endif
            ok(id,["automatic_updates":updatesEnabled && enabled])
        case "open_downloads":
            NSWorkspace.shared.open(URL(string:"https://github.com/U1XOvO/Haru/releases")!);ok(id)
        case "import_legacy":
            let panel=NSOpenPanel();panel.canChooseDirectories=true;panel.canChooseFiles=false;panel.allowsMultipleSelection=false
            panel.message="请选择已退出运行的旧版 Haru 仓库。只向空白安装导入，原目录保持不变。"
            panel.beginSheetModal(for:window){response in
                guard response == .OK,let source=panel.url else {self.ok(id,["cancelled":true]);return}
                self.processLock.lock();let busy = !self.backendProcesses.isEmpty;self.processLock.unlock()
                guard !busy,self.recorder?.isRecording != true else {self.fail(id,"请等待任务完成并停止录音后导入。");return}
                self.maintenanceMode=true
                self.maintenanceCompletions[id] = {result in
                    self.maintenanceMode = result["ok"] as? Bool == true
                    self.reply(id,result)
                }
                self.runBackend(id,"import_legacy",["source":source.path])
            }
        case "prepare_update", "recover_storage", "speech_prepare":
            fail(id,"此操作只能由应用内部发起。")
        case "study_pick":
            let panel=NSOpenPanel(); panel.allowsMultipleSelection=false; panel.canChooseDirectories=false
            panel.allowedContentTypes=[.json,.pdf,.mp3,.mpeg4Audio,.wav,.png,.jpeg]
            panel.beginSheetModal(for:window){response in
                guard response == .OK,let url=panel.url else{self.ok(id,["cancelled":true]);return}
                do {
                    let size=(try url.resourceValues(forKeys:[.fileSizeKey])).fileSize ?? 0
                    let ext=url.pathExtension.lowercased()
                    let limit=ext == "json" ? 95_000 : (["png","jpg","jpeg"].contains(ext) ? 2_000_000 : 50_000_000)
                    guard size>0,size<=limit else{self.fail(id,"JSON最大95KB，题图最大2MB，PDF或音频最大50MB。");return}
                    if ext == "json" {
                        let value=try JSONSerialization.jsonObject(with:Data(contentsOf:url))
                        self.ok(id,["paper":value,"name":url.lastPathComponent]);return
                    }
                    guard ["pdf","mp3","m4a","wav","png","jpg","jpeg"].contains(ext) else{self.fail(id,"不支持的文件类型。");return}
                    let folder=self.dataDir.appendingPathComponent("study-assets",isDirectory:true)
                    try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
                    let identity=UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()+"."+(ext == "jpeg" ? "jpg" : ext)
                    let destination=folder.appendingPathComponent(identity)
                    try Data(contentsOf:url).write(to:destination,options:.atomic)
                    self.ok(id,["asset":identity,"name":url.lastPathComponent])
                }catch{self.fail(id,"文件读取失败，尚未导入试卷。")}
            }
        case "study_open_asset":
            guard let identity=p["id"] as? String,
                  identity.range(of:"^[a-f0-9]{32}\\.(pdf|mp3|m4a|wav|png|jpg)$",options:.regularExpression) != nil else{fail(id,"媒体标识无效。");return}
            let folder=dataDir.appendingPathComponent("study-assets",isDirectory:true).resolvingSymlinksInPath()
            let file=folder.appendingPathComponent(identity).resolvingSymlinksInPath()
            guard file.deletingLastPathComponent().path==folder.path,FileManager.default.fileExists(atPath:file.path) else{fail(id,"媒体文件不存在。");return}
            if ["mp3","m4a","wav"].contains(file.pathExtension){
                do{stopAudio();player=try AVAudioPlayer(contentsOf:file);player?.play();ok(id)}
                catch{fail(id,"音频无法播放，请检查导入文件。")}
            }else{NSWorkspace.shared.open(file);ok(id)}
        case "study_stop_audio":
            stopAudio();ok(id)
        case "audio_toggle_pause":
            toggleAudioPause(id)
        case "audio_status":
            ok(id,["state":audioState()])
        case "speak":
            startSpeech(id,p)
        case "open_url":
            guard let raw=p["url"] as? String,let url=URL(string:raw),url.scheme=="https",
                  ["www.jpf.go.jp","www.jlpt.jp","bunpro.jp","www.irodori.jpf.go.jp"].contains(url.host ?? "") else{fail(id,"仅允许打开已核实的教学来源 HTTPS 地址。");return}
            NSWorkspace.shared.open(url);ok(id)
        case "reveal":
            guard let path=p["path"] as? String else{fail(id,"文件路径无效。");return}
            let url=URL(fileURLWithPath:path).resolvingSymlinksInPath()
            let exports=dataDir.appendingPathComponent("exports").resolvingSymlinksInPath().path+"/"
            guard url.path.hasPrefix(exports),FileManager.default.fileExists(atPath:url.path) else{fail(id,"只能显示应用导出的文件。");return}
            NSWorkspace.shared.activateFileViewerSelecting([url]);ok(id)
        case "record_start":
            if recorder?.isRecording == true {fail(id,"已经在录音。");return}
            AVAudioApplication.requestRecordPermission { allowed in
                DispatchQueue.main.async { if allowed {self.startRecording(id)} else {self.fail(id,"麦克风未获授权。可在系统设置 → 隐私与安全性 → 麦克风中允许 Haru。")} }
            }
        case "record_stop":
            recorder?.stop();recordingTimer?.invalidate();ok(id)
        case "record_play":
            if recorder?.isRecording == true {fail(id,"请先停止录音。");return}
            do {stopAudio();player=try AVAudioPlayer(contentsOf:dataDir.appendingPathComponent("speaking-latest.m4a")); player?.play();ok(id)}
            catch {fail(id,"还没有可回放的录音，请先录下你的跟读。")}
        default: runBackend(id,action,p)
        }
    }
    func startRecording(_ id:Int) {
        do {
            stopAudio()
            let file=dataDir.appendingPathComponent("speaking-latest.m4a")
            recorder=try AVAudioRecorder(url:file,settings:[AVFormatIDKey:kAudioFormatMPEG4AAC,AVSampleRateKey:44100,AVNumberOfChannelsKey:1,AVEncoderAudioQualityKey:AVAudioQuality.high.rawValue])
            guard recorder?.record(forDuration:60) == true else {fail(id,"无法启动麦克风，请检查音频输入设备。");return}
            recordingTimer?.invalidate()
            recordingTimer=Timer.scheduledTimer(withTimeInterval:60,repeats:false){_ in self.recorder?.stop();self.web.evaluateJavaScript("window.haruRecordingStopped()",completionHandler:nil)}
            ok(id)
        } catch {fail(id,"录音启动失败，请检查麦克风与本地文件权限。")}
    }
    func runBackend(_ id:Int,_ action:String,_ params:[String:Any], speechToken:UUID? = nil, completion:(([String:Any])->Void)? = nil) {
        let actions:Set<String>=["import_legacy","prepare_update","recover_storage","config_get","config_save","speech_settings_get","speech_settings_save","speech_voice_create","grammar_catalog","grammar_detail","grammar_mark","grammar_practice","study_catalog","study_import","study_generate","study_generation_start","study_generation_step","study_generation_status","study_generation_cancel","study_delete","study_start","study_attempt","study_save","study_history","study_mistakes","study_retry","study_summary","study_image","annotate","dictionary","dictionary_add","encounter","encounters","reading_lookup","knowledge","daily_word","daily_word_add","bootstrap","profile","lesson","grade","cards","card_queue","cards_page","card_detail","card_create","card_random","card_seed","review","decode","quiz","immersion","export","ping","history","curriculum","stage_assessment","remedial"]
        let finish: ([String:Any]) -> Void = { result in
            DispatchQueue.main.async {
                if let completion { completion(result) } else { self.reply(id,result) }
            }
        }
        guard actions.contains(action) || (action == "speech_prepare" && speechToken != nil),
              let input=try? JSONSerialization.data(withJSONObject:["action":action,"params":params]),input.count<=100000 else {
            finish(["ok":false,"error":"操作无效或输入过长。"]); return
        }
        let interpreter=python;let project=root!;let storage=dataDir!
        queue.async {
            if let token = speechToken, !self.isCurrentSpeech(token) { return }
            let process=Process(); process.executableURL=URL(fileURLWithPath:interpreter); process.arguments=self.bundledBackend ? [] : [project.appendingPathComponent("backend/bridge.py").path]; process.currentDirectoryURL=project
            var env=ProcessInfo.processInfo.environment;env["HARU_DATA_DIR"]=storage.path;env["HARU_STORAGE_DIR"]=self.storageRoot.path; env["PYTHONDONTWRITEBYTECODE"]="1";process.environment=env
            let stdin=Pipe();let stdout=Pipe();process.standardInput=stdin;process.standardOutput=stdout;process.standardError=FileHandle.nullDevice
            do {
                try process.run()
                guard self.registerBackendProcess(process, speechToken:speechToken) else {
                    self.stopBackendProcess(process, grace: 0.1)
                    process.waitUntilExit()
                    return
                }
                defer { self.unregisterBackendProcess(process) }
                stdin.fileHandleForWriting.write(input);try? stdin.fileHandleForWriting.close()
                let timedOut = DispatchSemaphore(value: 0)
                let watchdog=DispatchWorkItem {
                    if process.isRunning {
                        timedOut.signal()
                        self.stopBackendProcess(process)
                    }
                }
                let deadline = speechToken == nil ? self.backendDeadlineSeconds : 285
                DispatchQueue.global().asyncAfter(deadline:.now()+deadline,execute:watchdog)
                var result: [String:Any]?
                let data=stdout.fileHandleForReading.readDataToEndOfFile()
                result=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
                process.waitUntilExit();watchdog.cancel()
                let didTimeOut = timedOut.wait(timeout: .now()) == .success
                let speechData = result?["data"] as? [String:Any]
                if didTimeOut {
                    result=["ok":false,"error":speechToken == nil ? "操作超过10分钟上限，后台任务已停止。请确认任务状态后重试。" : "语音生成超时，请检查网络后重试。"]
                }
                if let token = speechToken, let data = speechData, let file = self.speechFile(data) {
                    // Load away from the UI thread and release transient files even when
                    // shutdown prevents the queued main-thread completion from running.
                    defer { if data["transient"] as? Bool == true { try? FileManager.default.removeItem(at:file) } }
                    if !didTimeOut && self.isCurrentSpeech(token) {
                        if let audio = try? Data(contentsOf:file) { result?["_speechAudio"] = audio }
                        else { result = ["ok":false,"error":"朗读音频不可用，请重试。"] }
                    }
                }
                finish(result ?? ["ok":false,"error":"本地服务未返回结果，请重新打开或重新安装 Haru。"])
            } catch { finish(["ok":false,"error":"无法启动 Haru 运行组件，请重新构建或重新安装。"]) }
        }
    }
    func finishSmoke(_ success:Bool) {
        guard let report=smokeReport else {return}
        let result:[String:Any] = ["ok":success,"bundled_backend":bundledBackend]
        if let data=try? JSONSerialization.data(withJSONObject:result) {try? data.write(to:report,options:.atomic)}
        smokeReport=nil
        NSApp.terminate(nil)
    }
    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!) {
        guard smokeReport != nil else {return}
        web.evaluateJavaScript("rpc('card_seed').then(r=>window.webkit.messageHandlers.haru.postMessage({id:-99,action:'smoke_result',params:{ok:r.length===5}})).catch(()=>window.webkit.messageHandlers.haru.postMessage({id:-99,action:'smoke_result',params:{ok:false}}));void 0",completionHandler:nil)
    }
    @objc func checkUpdates() {
        #if HARU_RELEASE
        if pendingInstall != nil {prepareInstallation();return}
        if let updaterController {updaterController.checkForUpdates(nil);return}
        #endif
        let alert=NSAlert();alert.messageText="此构建未启用自动更新";alert.informativeText="请从 Haru 发布页面下载新版安装包。源码版可 git pull 后重新运行启动脚本。";alert.runModal()
    }
    #if HARU_RELEASE
    func prepareInstallation() {
        web.evaluateJavaScript("window.haruPrepareUpdate && window.haruPrepareUpdate()") {ready,error in
            self.processLock.lock();let busy = !self.backendProcesses.isEmpty;self.processLock.unlock()
            guard ready as? Bool == true,!busy,self.recorder?.isRecording != true else {
                self.web.evaluateJavaScript("window.haruCancelUpdate && window.haruCancelUpdate()",completionHandler:nil)
                let alert=NSAlert();alert.messageText="更新已准备好";alert.informativeText="请完成录音、生成或考试后，再点击检查更新继续安装。";alert.runModal();return
            }
            self.maintenanceMode=true
            self.maintenanceCompletions[-1] = {result in
                if result["ok"] as? Bool == true {
                    let install=self.pendingInstall;self.pendingInstall=nil;install?()
                } else {
                    self.maintenanceMode=false
                    self.web.evaluateJavaScript("window.haruCancelUpdate && window.haruCancelUpdate()",completionHandler:nil)
                    let alert=NSAlert();alert.messageText="更新前备份未完成";alert.informativeText="请检查数据目录空间和权限，然后再次点击检查更新。";alert.runModal()
                }
            }
            self.runBackend(-1,"prepare_update",[:])
        }
    }
    #endif
    func webView(_ webView:WKWebView,decidePolicyFor navigationAction:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void) {
        guard let url=navigationAction.request.url else{decisionHandler(.cancel);return}
        if url.isFileURL && url.standardizedFileURL.path == root.appendingPathComponent("ui/index.html").standardizedFileURL.path {decisionHandler(.allow)} else {decisionHandler(.cancel)}
    }
}
#if HARU_RELEASE
extension HaruApp: SPUUpdaterDelegate {
    func updater(_ updater:SPUUpdater, didAbortWithError error:Error) {
        guard pendingInstall != nil || maintenanceCompletions[-1] != nil else {return}
        pendingInstall=nil
        maintenanceCompletions.removeValue(forKey:-1)
        maintenanceMode=false
        web.evaluateJavaScript("window.haruCancelUpdate && window.haruCancelUpdate()",completionHandler:nil)
    }
    func updater(_ updater:SPUUpdater, shouldPostponeRelaunchForUpdate item:SUAppcastItem, untilInvokingBlock installHandler:@escaping () -> Void) -> Bool {
        pendingInstall=installHandler
        prepareInstallation()
        return true
    }
}
#endif
let app=NSApplication.shared
let delegate=HaruApp()
app.delegate=delegate
app.setActivationPolicy(.regular)
app.run()
