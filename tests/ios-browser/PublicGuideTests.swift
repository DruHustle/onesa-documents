import XCTest

final class PublicGuideTests: XCTestCase {
    @MainActor func testTouchPublicSearchResult() throws {
        continueAfterFailure = false
        let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
        safari.activate()
        let web = safari.webViews.firstMatch
        XCTAssertTrue(web.waitForExistence(timeout: 45))

        // The responsive navbar includes another search field in its collapsed sidebar.
        // Bind each gesture to the field Safari can hit on the current viewport.
        // iOS 26.5 Safari may expose <input type="search"> as a
        // searchField/Other element rather than a textField. Search the full
        // accessibility subtree, then still require a hittable native input.
        let fields = web.descendants(matching: .any).matching(NSPredicate(
            format: "label CONTAINS[c] %@ OR placeholderValue CONTAINS[c] %@", "Search", "Search docs"
        ))
        func visibleField() -> XCUIElement? {
            fields.allElementsBoundByIndex.first {
                $0.isHittable && ($0.elementType == .searchField || $0.elementType == .textField)
            }
        }
        func waitForSearch() -> XCUIElement? {
            let deadline = Date().addingTimeInterval(30)
            repeat {
                if let field = visibleField() { return field }
                RunLoop.current.run(until: Date().addingTimeInterval(0.25))
            } while Date() < deadline
            return nil
        }
        var target = waitForSearch()
        if target == nil {
            let error = web.staticTexts.matching(NSPredicate(
                format: "label BEGINSWITH[c] %@ AND label CONTAINS[c] %@ AND label ENDSWITH[c] %@",
                "Safari couldn", "the server stopped", "responding."
            )).firstMatch
            let address = safari.textFields["TabBarItemTitle"]
            let reload = safari.buttons["ReloadButton"]
            // Retry this read once only when Safari identifies a transport error
            // at the public origin and no guide or typed query has been visited.
            if error.exists && address.exists && reload.exists && reload.isHittable {
                let value = (address.value as? String)?.trimmingCharacters(
                    in: CharacterSet(charactersIn: "\u{200E}\u{200F} ").union(.whitespacesAndNewlines))
                if value == "docs.onesa.eu" || value == "https://docs.onesa.eu/" {
                    reload.tap()
                    target = waitForSearch()
                }
            }
        }
        let initialField = try XCTUnwrap(target, "The public homepage did not expose its search input")
        // Safari can expose both navbar copies of the input. Once the viewport
        // moves for the keyboard, an index-based field can resolve to the other
        // copy. Give the tapped field focus and check it received the input.
        initialField.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let keyboard = safari.keyboards.firstMatch
        if !keyboard.waitForExistence(timeout: 8) {
            let field = try XCTUnwrap(visibleField(), "The search field disappeared")
            field.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        XCTAssertTrue(keyboard.waitForExistence(timeout: 10), "Tapping search did not open the iPhone keyboard")
        // On a fresh simulator, the QuickPath introduction can cover the
        // keyboard without presenting an XCTest alert.
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for owner in [safari, springboard] {
            let tutorial = owner.descendants(matching: .any)["UIContinuousPathIntroductionView"].firstMatch
            if !tutorial.exists { continue }
            let explanation = owner.staticTexts["Speed up your typing by sliding your finger across the letters to compose a word."]
            guard explanation.exists else { continue }
            let action = owner.buttons.matching(NSPredicate(format: "label == %@", "Continue"))
                .allElementsBoundByIndex.first { $0.isHittable && tutorial.frame.intersects($0.frame)
                    && $0.frame.minY >= explanation.frame.maxY }
            try XCTUnwrap(action, "The keyboard tutorial did not expose its Continue button").tap()
            XCTAssertTrue(tutorial.waitForNonExistence(timeout: 10))
            break
        }

        // WebKit may expose zero-size keys or accept bulk XCTest typing without
        // changing the field. Synchronize native input against the visible value.
        let query = "quickstart"
        let typed = fields.matching(NSPredicate(format: "value ==[c] %@", query)).firstMatch
        safari.typeText(query)
        var prefix = query
        if !typed.waitForExistence(timeout: 5) {
            let input = try XCTUnwrap(visibleField(), "The focused documentation search disappeared")
            let value = (input.value as? String) ?? ""
            prefix = value == input.placeholderValue ? "" : value.lowercased()
            XCTAssertTrue(query.hasPrefix(prefix), "The search field received unexpected text")
        }
        for letter in query.dropFirst(prefix.count) {
            let key = String(letter)
            let next = prefix + key
            let received = fields.matching(NSPredicate(format: "value ==[c] %@", next)).firstMatch
            web.typeText(key)
            if !received.waitForExistence(timeout: 4) {
                let input = try XCTUnwrap(visibleField())
                let value = (input.value as? String) ?? ""
                let unchanged = value.lowercased() == prefix || (prefix.isEmpty && value == input.placeholderValue)
                XCTAssertTrue(unchanged, "The search field received unexpected native input")
                let keys = keyboard.keys.matching(NSPredicate(
                    format: "label ==[c] %@ OR identifier ==[c] %@", key, key
                )).allElementsBoundByIndex
                if let visibleKey = keys.first(where: { !$0.frame.isEmpty && keyboard.frame.contains($0.frame) && $0.isHittable }) {
                    let frame = visibleKey.frame
                    let origin = safari.frame.origin
                    let offset = CGVector(dx: frame.midX - origin.x, dy: frame.midY - origin.y)
                    safari.coordinate(withNormalizedOffset: .zero).withOffset(offset).tap()
                } else {
                    // Restore focus to this search field before repeating only
                    // the unobserved character through the native keyboard.
                    input.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
                    web.typeText(key)
                }
                XCTAssertTrue(received.waitForExistence(timeout: 5), "The native keyboard did not update the search field")
            }
            prefix = next
        }
        XCTAssertTrue(typed.waitForExistence(timeout: 10) && typed.isHittable,
                      "The visible documentation search did not receive the keyboard input")
        // iOS 26 Safari may classify web links as Other rather than Link in
        // the XCTest accessibility tree (see failed run 43664871821). Find a
        // visible result by its accessible label, but still require a real tap
        // and the guide's unique post-navigation content before passing.
        let region = web.descendants(matching: .any).matching(NSPredicate(
            format: "label BEGINSWITH[c] %@", "Search results"
        )).firstMatch
        XCTAssertTrue(region.waitForExistence(timeout: 15))
        let candidates = region.descendants(matching: .any).matching(NSPredicate(
            format: "label BEGINSWITH[c] %@", "Developer quickstart"
        ))
        let resultDeadline = Date().addingTimeInterval(35)
        var visibleResult: XCUIElement?
        while Date() < resultDeadline {
            let matches = candidates.allElementsBoundByIndex
            visibleResult = matches.first { $0.elementType == .link && $0.isHittable }
                ?? matches.first { $0.elementType == .other && $0.isHittable }
            if visibleResult != nil { break }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        }
        guard let result = visibleResult else {
            XCTFail("No accessible, hittable public quickstart search result was found")
            return
        }
        print("PUBLIC_GUIDE_NATIVE_SEARCH_RESULT_FOUND")
        // Safari can return an activation point on the screen edge for a wide
        // search result at keyboard zoom. Tap the inside of the visible card.
        var visible = result.frame.intersection(region.frame).intersection(web.frame).intersection(safari.frame)
        if keyboard.exists {
            let keys = keyboard.frame
            if !keys.isEmpty && keys.intersects(visible) {
                visible.size.height = max(0, keys.minY - visible.minY)
            }
        }
        for toolbar in safari.toolbars.allElementsBoundByIndex {
            let covered = visible.intersection(toolbar.frame)
            if covered.isNull || covered.isEmpty { continue }
            let above = covered.minY - visible.minY
            let below = visible.maxY - covered.maxY
            if above >= below {
                visible.size.height = above
            } else {
                visible.origin.y = covered.maxY
                visible.size.height = below
            }
        }
        XCTAssertGreaterThan(visible.width, 8)
        XCTAssertGreaterThan(visible.height, 8)
        let origin = safari.frame.origin
        let point = CGVector(dx: visible.midX - origin.x, dy: visible.midY - origin.y)
        safari.coordinate(withNormalizedOffset: .zero).withOffset(point).tap()
        let guide = web.descendants(matching: .any).matching(NSPredicate(
            format: "label CONTAINS[c] %@", "Docker-only application startup"
        )).firstMatch
        if !guide.waitForExistence(timeout: 12) {
            web.swipeUp()
        }
        XCTAssertTrue(guide.waitForExistence(timeout: 15), "The native tap did not expose the guide's Docker-only application startup section")
        print("PUBLIC_GUIDE_NATIVE_TAP_OPENED_GUIDE")
    }
}
