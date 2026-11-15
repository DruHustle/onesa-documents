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
        let fields = web.textFields.matching(NSPredicate(
            format: "label CONTAINS[c] %@ OR placeholderValue CONTAINS[c] %@", "Search", "Search docs"
        ))
        func visibleField() -> XCUIElement? {
            fields.allElementsBoundByIndex.first { $0.isHittable }
        }
        let deadline = Date().addingTimeInterval(30)
        while visibleField() == nil && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
        }
        try XCTUnwrap(visibleField(), "The public page did not expose its visible search field").tap()

        let input = try XCTUnwrap(visibleField(), "The visible search field disappeared after opening")
        input.tap()
        input.typeText("quickstart")
        let typed = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "value == %@", "quickstart"), object: input
        )
        XCTAssertEqual(XCTWaiter.wait(for: [typed], timeout: 10), .completed,
                       "The real iPhone keyboard did not enter the expected search")
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
