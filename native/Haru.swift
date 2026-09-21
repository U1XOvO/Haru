import AppKit
import WebKit
import AVFoundation
import UniformTypeIdentifiers
import Darwin

final class HaruApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var root: URL!
    var python: String = ""
    var dataDir: URL!
    let speech = AVSpeechSynthesizer()
    var recorder: AVAudioRecorder?
    var player: AVAudioPlayer?
    var recordingTimer: Timer?
    let processLock = NSLock()
    var chatProcesses: [String: Process] = [:]
    var backendProcesses: [Int32: Process] = [:]
    var shuttingDown = false
    let queue = DispatchQueue(label: "haru.backend", attributes: .concurrent)
    let backendDeadlineSeconds: TimeInterval = 600

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let iconURL = Bundle.main.url(forResource: "Haru", withExtension: "icns"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
        guard let resource = Bundle.main.resourceURL,
              let configData = try? Data(contentsOf: resource.appendingPathComponent("runtime.json")),
              let config = try? JSONSerialization.jsonObject(with: configData) as? [String:String],
              let rootPath = config["root"], let pythonPath = config["python"] else {
            fatalAlert("请在项目目录运行 scripts/build_app.sh 重新构建应用。"); return
        }
        root = URL(fileURLWithPath: rootPath, isDirectory: true)
        python = pythonPath
        dataDir = URL(fileURLWithPath: ProcessInfo.processInfo.environment["HARU_DATA_DIR"] ?? root.appendingPathComponent("runtime").path, isDirectory: true)
        guard FileManager.default.isExecutableFile(atPath: python) else {fatalAlert("找不到项目 Python 环境。请关闭应用并重新运行 start.command 自动修复。"); return}
        try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        let menu = NSMenu(); let appMenu = NSMenu(); let appItem = NSMenuItem()
        appItem.submenu = appMenu; menu.addItem(appItem)
        appMenu.addItem(withTitle: "关于 Haru", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator()); appMenu.addItem(withTitle: "退出 Haru", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = NSMenu(title: "编辑"); let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: ""); editItem.submenu = edit; menu.addItem(editItem)
        for (name, action, key) in [("撤销", "undo:", "z"), ("重做", "redo:", "Z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] { edit.addItem(withTitle:name, action:Selector(action), keyEquivalent:key) }
        NSApp.mainMenu = menu
        let configView = WKWebViewConfiguration()
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
    }
    func fatalAlert(_ message:String) {let a=NSAlert(); a.messageText="Haru 暂时无法启动"; a.informativeText=message; a.runModal(); NSApp.terminate(nil)}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
    func applicationWillTerminate(_ notification: Notification) {
        recorder?.stop(); player?.stop(); speech.stopSpeaking(at:.immediate)
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
        guard let data=try? JSONSerialization.data(withJSONObject:result,options:[.fragmentsAllowed]),let json=String(data:data,encoding:.utf8) else{return}
        web.evaluateJavaScript("window.haruResolve(\(id),\(json))",completionHandler:nil)
    }
    func ok(_ id:Int,_ data:Any = [String:String]()) {reply(id,["ok":true,"data":data])}
    func fail(_ id:Int,_ error:String) {reply(id,["ok":false,"error":error])}
    func registerBackendProcess(_ process: Process) -> Bool {
        processLock.lock(); defer { processLock.unlock() }
        guard !shuttingDown else { return false }
        backendProcesses[process.processIdentifier] = process
        return true
    }
    func unregisterBackendProcess(_ process: Process) {
        processLock.lock(); backendProcesses.removeValue(forKey: process.processIdentifier); processLock.unlock()
    }
    func stopBackendProcess(_ process: Process, grace: TimeInterval = 1.0) {
        guard process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(grace)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.025) }
        if process.isRunning { _ = kill(process.processIdentifier, SIGKILL) }
    }
    func userContentController(_ userContentController: WKUserContentController,didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.request.url?.isFileURL == true,
              message.frameInfo.request.url?.standardizedFileURL.path == root.appendingPathComponent("ui/index.html").standardizedFileURL.path,
              let m=message.body as? [String:Any],let id=m["id"] as? Int,let action=m["action"] as? String else{return}
        let p=m["params"] as? [String:Any] ?? [:]
        switch action {
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
                do{player?.stop();speech.stopSpeaking(at:.immediate);player=try AVAudioPlayer(contentsOf:file);player?.play();ok(id)}
                catch{fail(id,"音频无法播放，请检查导入文件。")}
            }else{NSWorkspace.shared.open(file);ok(id)}
        case "study_stop_audio":
            player?.stop();speech.stopSpeaking(at:.immediate);ok(id)
        case "speak":
            guard let text=p["text"] as? String,!text.isEmpty,text.count<=6000 else{fail(id,"朗读文本无效。");return}
            guard let voice=AVSpeechSynthesisVoice(language:"ja-JP") else{fail(id,"未找到日语语音，请在系统设置的辅助功能中添加日语语音。");return}
            speech.stopSpeaking(at:.immediate)
            let utterance=AVSpeechUtterance(string:text); utterance.voice=voice; utterance.rate=0.42; speech.speak(utterance); ok(id)
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
            do {player=try AVAudioPlayer(contentsOf:dataDir.appendingPathComponent("speaking-latest.m4a")); player?.play();ok(id)}
            catch {fail(id,"还没有可回放的录音，请先录下你的跟读。")}
        default: runBackend(id,action,p)
        }
    }
    func startRecording(_ id:Int) {
        do {
            player?.stop();speech.stopSpeaking(at:.immediate)
            let file=dataDir.appendingPathComponent("speaking-latest.m4a")
            recorder=try AVAudioRecorder(url:file,settings:[AVFormatIDKey:kAudioFormatMPEG4AAC,AVSampleRateKey:44100,AVNumberOfChannelsKey:1,AVEncoderAudioQualityKey:AVAudioQuality.high.rawValue])
            guard recorder?.record(forDuration:60) == true else {fail(id,"无法启动麦克风，请检查音频输入设备。");return}
            recordingTimer?.invalidate()
            recordingTimer=Timer.scheduledTimer(withTimeInterval:60,repeats:false){_ in self.recorder?.stop();self.web.evaluateJavaScript("window.haruRecordingStopped()",completionHandler:nil)}
            ok(id)
        } catch {fail(id,"录音启动失败，请检查麦克风与本地文件权限。")}
    }
    func runBackend(_ id:Int,_ action:String,_ params:[String:Any]) {
        let actions:Set<String>=["config_get","config_save","grammar_catalog","grammar_detail","grammar_mark","grammar_practice","study_catalog","study_import","study_generate","study_generation_start","study_generation_step","study_generation_status","study_generation_cancel","study_delete","study_start","study_attempt","study_save","study_history","study_mistakes","study_retry","study_summary","study_image","annotate","dictionary","dictionary_add","encounter","knowledge","chat_state","chat_start","chat_finish","chat_memory","chat_stream","chat_cancel","daily_word","daily_word_add","bootstrap","profile","lesson","grade","cards","card_create","card_random","card_seed","review","chat","chat_history","decode","quiz","immersion","export","ping","history","curriculum","stage_assessment","remedial"]
        guard actions.contains(action),let input=try? JSONSerialization.data(withJSONObject:["action":action,"params":params]),input.count<=100000 else{fail(id,"操作无效或输入过长。");return}
        let interpreter=python;let project=root!;let storage=dataDir!
        queue.async {
            let process=Process(); process.executableURL=URL(fileURLWithPath:interpreter); process.arguments=[project.appendingPathComponent("backend/bridge.py").path]; process.currentDirectoryURL=project
            var env=ProcessInfo.processInfo.environment;env["HARU_DATA_DIR"]=storage.path; env["PYTHONDONTWRITEBYTECODE"]="1";process.environment=env
            let stdin=Pipe();let stdout=Pipe();process.standardInput=stdin;process.standardOutput=stdout;process.standardError=FileHandle.nullDevice
            do {
                try process.run()
                guard self.registerBackendProcess(process) else {
                    self.stopBackendProcess(process, grace: 0.1)
                    process.waitUntilExit()
                    return
                }
                defer { self.unregisterBackendProcess(process) }
                let requestID=params["request_id"] as? String ?? ""
                if action == "chat_stream" {self.processLock.lock();self.chatProcesses[requestID]=process;self.processLock.unlock()}
                defer {if action == "chat_stream" {self.processLock.lock();self.chatProcesses.removeValue(forKey:requestID);self.processLock.unlock()}}
                stdin.fileHandleForWriting.write(input);try? stdin.fileHandleForWriting.close()
                let timedOut = DispatchSemaphore(value: 0)
                let watchdog=DispatchWorkItem {
                    if process.isRunning {
                        timedOut.signal()
                        self.stopBackendProcess(process)
                    }
                }
                DispatchQueue.global().asyncAfter(deadline:.now()+self.backendDeadlineSeconds,execute:watchdog)
                var result: [String:Any]?
                if action == "chat_stream" {
                    var buffer=Data()
                    while true {
                        let chunk=stdout.fileHandleForReading.availableData
                        if chunk.isEmpty {break}
                        buffer.append(chunk)
                        if buffer.count > 2_000_000 {self.stopBackendProcess(process);break}
                        while let newline=buffer.firstIndex(of:10) {
                            let line=Data(buffer[..<newline]);buffer.removeSubrange(...newline)
                            if let event=(try? JSONSerialization.jsonObject(with:line)) as? [String:Any] {
                                if event["type"] as? String == "delta",let json=String(data:line,encoding:.utf8) {
                                    DispatchQueue.main.async {self.web.evaluateJavaScript("window.haruStream(\(id),\(json))",completionHandler:nil)}
                                } else {result=event}
                            }
                        }
                    }
                } else {
                    let data=stdout.fileHandleForReading.readDataToEndOfFile()
                    result=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
                }
                process.waitUntilExit();watchdog.cancel()
                let didTimeOut = timedOut.wait(timeout: .now()) == .success
                if action == "chat_cancel", let data=result?["data"] as? [String:Any],data["status"] as? String == "cancelled" {
                    self.processLock.lock()
                    let active=self.chatProcesses[requestID]
                    self.processLock.unlock()
                    if let active { self.stopBackendProcess(active) }
                }
                if didTimeOut {
                    result=["ok":false,"error":"操作超过10分钟上限，后台任务已停止。请确认任务状态后重试。"]
                } else if action == "chat_stream",result == nil {result=["ok":false,"error":"生成已停止或连接中断，本轮未保存。"]}
                DispatchQueue.main.async {if let r=result{self.reply(id,r)}else{self.fail(id,"本地服务未返回结果。请重新运行 start.command 检查项目环境。")}}
            } catch {DispatchQueue.main.async{self.fail(id,"无法启动项目 Python，请关闭应用并重新运行 start.command。")}}
        }
    }
    func webView(_ webView:WKWebView,decidePolicyFor navigationAction:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void) {
        guard let url=navigationAction.request.url else{decisionHandler(.cancel);return}
        if url.isFileURL && url.standardizedFileURL.path == root.appendingPathComponent("ui/index.html").standardizedFileURL.path {decisionHandler(.allow)} else {decisionHandler(.cancel)}
    }
}
let app=NSApplication.shared
let delegate=HaruApp()
app.delegate=delegate
app.setActivationPolicy(.regular)
app.run()
