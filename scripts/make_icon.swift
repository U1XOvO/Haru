import AppKit
let output=CommandLine.arguments[1]
let size=1024
let rep=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:size,pixelsHigh:size,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
let context=NSGraphicsContext(bitmapImageRep:rep)!
NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=context
let rect=NSRect(x:50,y:50,width:924,height:924)
let shape=NSBezierPath(roundedRect:rect,xRadius:216,yRadius:216)
NSGradient(starting:NSColor(calibratedRed:0.97,green:0.84,blue:0.9,alpha:1),ending:NSColor(calibratedRed:0.81,green:0.87,blue:0.98,alpha:1))!.draw(in:shape,angle:-40)
let font=NSFont(name:"HiraginoSans-W4",size:540) ?? NSFont.systemFont(ofSize:540)
let attrs:[NSAttributedString.Key:Any]=[.font:font,.foregroundColor:NSColor(calibratedRed:0.45,green:0.44,blue:0.57,alpha:1)]
let text="は" as NSString
let bounds=text.size(withAttributes:attrs)
text.draw(at:NSPoint(x:(1024-bounds.width)/2,y:(1024-bounds.height)/2+10),withAttributes:attrs)
let flower="✳" as NSString
flower.draw(at:NSPoint(x:747,y:734),withAttributes:[.font:NSFont.systemFont(ofSize:105),.foregroundColor:NSColor.white.withAlphaComponent(0.85)])
NSGraphicsContext.restoreGraphicsState()
try rep.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:output))
