-- Prism droplet
-- Drop .3mf files on the app: pick a printer from the list, review the
-- model analysis, pick Speed / Balanced / Quality, get "<name> - KEY.3mf"
-- next to each original.

on open theItems
	set fileList to {}
	repeat with f in theItems
		set p to POSIX path of f
		if p ends with ".3mf" then set end of fileList to p
	end repeat
	if (count of fileList) is 0 then
		display dialog "Drop .3mf files onto this app." buttons {"OK"} default button "OK" with title "Prism"
		return
	end if
	processFiles(fileList)
end open

-- Double-clicked with no files: open the app window. It is a local page in the
-- default browser rather than a Tk dialog, so it looks the same on macOS and
-- Windows and needs nothing installed. Backgrounded, so this applet can quit
-- and leave the window as the app; the server exits once the tab closes.
on run
	set b to bundledEngine()
	if b is not "" then
		set guiCmd to quoted form of b
	else
		set guiCmd to "/usr/bin/python3 " & quoted form of (resourcesDir() & "gui.py")
	end if
	do shell script guiCmd & " > /dev/null 2>&1 &"
end run

-- /usr/bin/python3 is NOT Python. It is the xcode-select shim, byte-identical
-- to /usr/bin/git and /usr/bin/clang, and on a Mac without Xcode or the Command
-- Line Tools it prompts to install developer tools instead of running anything.
-- So prefer the bundled engine, which carries its own interpreter and needs
-- nothing installed. The fallback exists for a source checkout, where
-- macos/build.sh had no binary to embed.
on resourcesDir()
	return POSIX path of (path to me) & "Contents/Resources/"
end resourcesDir

-- Actually RUN it rather than testing the executable bit. The bundled engine is
-- built for one architecture, so on a Mac of the other kind the file is present
-- and executable and still cannot start ("Bad CPU type"). Only an exec proves
-- it. Called once per launch, so the cost is one engine start.
on bundledEngine()
	set b to resourcesDir() & "prism-engine"
	try
		do shell script quoted form of b & " --engine --list > /dev/null 2>&1"
		return b
	end try
	return ""
end bundledEngine

on enginePath()
	set b to bundledEngine()
	if b is not "" then return quoted form of b & " --engine"
	return "/usr/bin/python3 " & quoted form of (resourcesDir() & "optimise3mf.py")
end enginePath

on processFiles(fileList)
	set tool to enginePath()
	-- printer list from the engine's registry
	set listText to do shell script tool & " --list"
	set printerLines to paragraphs of listText
	set chosen to choose from list printerLines with prompt "Optimise for which printer?" default items {item 1 of printerLines} with title "Prism"
	if chosen is false then return
	set chosenLine to item 1 of chosen
	set printerKey to word 1 of chosenLine
	-- model analysis + mode choice
	set fileArgs to ""
	repeat with p in fileList
		set fileArgs to fileArgs & " " & quoted form of p
	end repeat
	set reportText to do shell script tool & " --printer " & printerKey & " --report" & fileArgs
	set modeChoice to choose from list {"speed  - fastest, coarser layers", "balanced  - the sensible default", "quality  - finest layers, ironing, dome refinement"} with prompt "Model analysis:" & return & return & reportText & return & return & "Pick a mode:" default items {"balanced  - the sensible default"} with title "Prism"
	if modeChoice is false then return
	set modeKey to word 1 of (item 1 of modeChoice)
	-- Full Spectrum: offered only for printers whose --list line advertises it
	set spectrumFlag to ""
	set spectrumLabel to ""
	if chosenLine contains "[Full Spectrum]" then
		set fsOptions to {"Standard colours - one filament per slot", "Full Spectrum - blend the four filaments into a wide palette"}
		set colourChoice to choose from list fsOptions with prompt "Colour handling:" & return & return & "Full Spectrum loads Cyan, Magenta, Yellow and Grey, then builds the blended palette for you. It needs the semi-translucent Full Spectrum filaments and uses noticeably more filament in the prime tower." default items {item 1 of fsOptions} with title "Prism"
		if colourChoice is false then return
		if (item 1 of colourChoice) starts with "Full Spectrum" then
			set spectrumFlag to " --spectrum"
			set spectrumLabel to " / full spectrum"
			-- palette comes from the engine, so it always matches what gets written
			set palText to do shell script tool & " --printer " & printerKey & " --spectrum-list"
			set palLines to paragraphs of palText
			set autoItem to "Map the model's own colours automatically"
			set palOptions to {autoItem}
			repeat with i from 2 to (count of palLines)
				set aLine to item i of palLines
				if aLine is not "" then set end of palOptions to aLine
			end repeat
			-- a model using one slot has nothing to map, so don't default to mapping
			set colourCount to 1
			try
				set colourCount to (do shell script tool & " --spectrum-probe" & fileArgs) as integer
			end try
			if colourCount > 1 and (count of palOptions) > 1 then
				set defItem to autoItem
				set colourPrompt to "Colour:" & return & return & "This file carries " & colourCount & " colours. Mapping matches each one to the nearest blend. Or pick a single colour for the whole model."
			else
				set defItem to autoItem
				if (count of palOptions) > 1 then set defItem to item 2 of palOptions
				set colourPrompt to "Colour:" & return & return & "This file is a single colour, so there is nothing to map. Pick the colour to print it in."
			end if
			set pickColour to choose from list palOptions with prompt colourPrompt default items {defItem} with title "Prism"
			if pickColour is false then return
			set pickedLine to item 1 of pickColour
			if pickedLine is not autoItem then
				set spectrumFlag to spectrumFlag & " --spectrum-colour " & (word 1 of pickedLine)
				set spectrumLabel to " / " & (word 2 of pickedLine)
			end if
		end if
	end if
	set summaries to {}
	repeat with p in fileList
		try
			set outText to do shell script tool & " --printer " & printerKey & " --mode " & modeKey & spectrumFlag & " " & quoted form of p
			set end of summaries to outText
		on error errMsg
			set end of summaries to "FAILED: " & p & return & errMsg
		end try
	end repeat
	set AppleScript's text item delimiters to (return & return)
	set finalText to summaries as text
	set AppleScript's text item delimiters to ""
	display dialog finalText buttons {"Reveal in Finder", "OK"} default button "OK" with title "Prism - " & printerKey & " / " & modeKey & spectrumLabel
	if button returned of result is "Reveal in Finder" then
		set firstLine to paragraph 1 of (item 1 of summaries)
		if firstLine starts with "OK -> " then
			set outPath to text 7 thru -1 of firstLine
			tell application "Finder"
				reveal (POSIX file outPath as alias)
				activate
			end tell
		end if
	end if
end processFiles
