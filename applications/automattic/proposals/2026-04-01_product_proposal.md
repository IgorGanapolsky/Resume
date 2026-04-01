# Product Proposal: Automattic - Jetpack Mobile Analytics Module

**To:** Engineering Manager, Mobile Apps
**From:** Igor Ganapolsky
**Date:** 2026-04-01

## 1. The Observation

I noticed that the WordPress mobile app heavily relies on legacy native views for analytics, which leads to slower iteration cycles and inconsistent feature parity between iOS and Android. For creators and businesses depending on real-time traffic data, the lack of a modern, unified charting library causes frustration and slower feature rollouts.

## 2. The Recommendation: Unified React Native/Skia Analytics Module

Migrate the Jetpack analytics dashboard to a unified React Native module utilizing `react-native-skia`.

- **User Impact:** 60fps smooth charting, interactive gestures (scrubbing data points), and faster loading times.
- **Business Impact:** Drastically reduces engineering overhead by maintaining a single high-performance codebase for complex visualizations across both platforms.
- **Technical Path:** Implement a localized TurboModule wrapping the Skia engine, effectively bypassing the React Native bridge for high-frequency gesture data.

## 3. The Prototype (The Proof)

I have built a minimal working prototype demonstrating a 60fps interactive line chart using Skia that mirrors the current WordPress dashboard layout but with a unified codebase.

- **Prototype Link:** https://github.com/IgorGanapolsky/rn-skia-analytics-poc
- **Key Insight:** Proves that we can achieve native-level performance and gesture handling in a shared module without rewriting custom charting logic for SwiftUI and Jetpack Compose.

---

## 4. Why This Matters to Automattic

In my previous work as Team Lead Mobile Engineer at Subway, I led the migration of a 5M+ user app to the New Architecture (TurboModules, Fabric), solving similar rendering bottlenecks for complex UI states. Bringing that specific expertise in high-performance, cross-platform architecture to the Mobile Engineer position is why I'm excited to apply to Automattic.

---

**Links:**

- **Resume:** [Attach Tailored Resume]
- **Portfolio:** https://github.com/IgorGanapolsky
