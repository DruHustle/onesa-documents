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
        let deadline = Date().addingTimeInterval(30)
        while visibleField() == nil && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        guard let initialField = visibleField() else {
            let snapshot = web.descendants(matching: .any).allElementsBoundByIndex.prefix(80)
                .map { "\($0.elementType):\($0.label):\($0.placeholderValue ?? "")" }
                .joined(separator: " | ")
            XCTFail("The public page did not expose a visible native search field. AX: \(snapshot)")
            return
        }
        // Safari can expose both navbar copies of the input. Once the viewport
        // moves for the keyboard, an index-based field can resolve to the other
        // copy. Send key taps to the keyboard, then check the visible field.
        initialField.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let keyboard = safari.keyboards.firstMatch
        if !keyboard.waitForExistence(timeout: 8) {
            let field = try XCTUnwrap(visibleField(), "The search field disappeared")
            field.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        XCTAssertTrue(keyboard.waitForExistence(timeout: 10), "Tapping search did not open the iPhone keyboard")
        for letter in "quickstart" {
            let key = keyboard.keys.matching(NSPredicate(
                format: "label ==[c] %@ OR identifier ==[c] %@", String(letter), String(letter)
            )).firstMatch
            XCTAssertTrue(key.waitForExistence(timeout: 5), "The iPhone keyboard did not offer the expected key")
            key.tap()
        }
        let typed = fields.matching(NSPredicate(format: "value ==[c] %@", "quickstart")).firstMatch
        XCTAssertTrue(typed.waitForExistence(timeout: 10) && typed.isHittable,
                      "The visible documentation search did not receive the keyboard input")
        // iOS 26 Safari may classify web links as Other rather than Link in
        // the XCTest accessibility tree (see failed run 43664871821). Find a
        // visible result by its accessible label, but still require a real tap
        // and the guide's unique post-navigation content before passing.
        let candidates = safari.descendants(matching: .any).matching(NSPredicate(
            format: "label CONTAINS[c] %@", "Developer quickstart"
        ))
        let resultDeadline = Date().addingTimeInterval(35)
        var visibleResult: XCUIElement?
        while Date() < resultDeadline {
            visibleResult = candidates.allElementsBoundByIndex.first { $0.isHittable }
            if visibleResult != nil { break }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        }
        guard let result = visibleResult else {
            XCTFail("No accessible, hittable public quickstart search result was found")
            return
        }
        print("PUBLIC_GUIDE_NATIVE_SEARCH_RESULT_FOUND")
        result.tap()
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
