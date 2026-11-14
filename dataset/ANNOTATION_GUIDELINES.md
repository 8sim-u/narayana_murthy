# Ground-Truth Annotation Guidelines: Person Detection

These guidelines establish unambiguous ground-truth bounding box placement for the WAMSS person detection evaluation dataset.

---

## 1. Core Principles

1. **Human Ground Truth Only:** Annotations must represent actual human presence observed by a human annotator. Never use automated detector predictions as substitute ground truth.
2. **Visible Extent Rule:** Bounding boxes must enclose only the **visible** portions of a person. Do **not** hallucinate or invent bounding box coordinates for invisible anatomy hidden behind solid desks, monitors, walls, or opaque barriers.
3. **Tight Boundaries:** The bounding box should form the tightest axis-aligned rectangle enclosing the subject's visible head, hair, limbs, and clothing.

---

## 2. Guidelines by Scenario

### A. Fully Visible Standing or Walking Person
- **Extent:** Top of head/hair to lowest contact point of shoes/feet.
- **Width:** Widest visible span (elbows, outstretched hands, edge of jacket/clothing).
- **Rule:** Keep margins minimal ($\le 2$ pixels). Include hats, glasses, and worn backpacks/jackets.

### B. Seated Person at Workstation
- **Extent:** Top of head/hair down to the lowest visible portion of the thighs, knees, or feet.
- **Under-desk Occlusion:** If the lower torso and legs are completely hidden beneath a desk, end the bounding box at the desk surface or lowest visible clothing edge. Do **not** extend the box down to where you assume the feet are.
- **Arms & Hands on Keyboard:** Include hands and forearms resting on the desk or typing on a keyboard as part of the person box.
- **Office Chairs:** Do **not** annotate the chair as part of the person. If chair armrests or backrests poke out beyond shoulders, do not expand the person box to include the chair frame.

### C. Partially Occluded Person
- **By Objects (Monitors, Laptops, Partitions):**
  - If a person is split into two visible fragments by a narrow pole or monitor cable, draw **one single bounding box** enclosing both fragments.
  - If a person is substantially occluded (e.g., only head and shoulders visible above a privacy cubicle wall), draw the box tightly around the visible upper body only.
- **By Another Person:**
  - Each individual gets their own bounding box enclosing their visible extents. Overlapping bounding boxes are normal and expected in multi-person scenes.

### D. Person Partially Outside the Camera Frame (Boundary Clipping)
- When a person enters, exits, or leans partially out of frame:
  - Extend the box to the exact edge of the image ($0.0$ or $1.0$).
  - Never place coordinates outside the image frame ($< 0.0$ or $> 1.0$).
  - Minimum visibility criterion: Annotate the person if at least $\ge 15\%$ of their body (or their head/face) is visibly inside the frame.

### E. Small or Distant People
- Small or distant individuals must be annotated if they can be reliably identified as a human by a human viewer.
- Minimum bounding box dimension: Box must be at least $10\times 10$ pixels ($w \ge 0.015, h \ge 0.02$ at $640\times 480$). Anything smaller where identity is ambiguous should be documented as an ambiguous region.

### F. Extreme Ambiguity & Difficult Cases
- If a shape resembles a person (e.g., jacket draped over a chair) but the human reviewer cannot confirm human presence:
  - **Do NOT annotate it as a person.**
  - Document the frame and explanation in the session manifest under `"notes"`.
- Reflections in glass windows or mirrors: **Do NOT annotate reflections** as people. Only annotate physical bodies.
- Photographs, posters, or faces on computer screens: **Do NOT annotate 2D representations**.

### G. Empty Scenes (Zero People)
- When no person is visible anywhere in the frame:
  - Create an empty text file `CAM_<ID>_<session>_<frame>.txt` containing 0 bytes.
  - Do **not** omit the `.txt` file. An empty file explicitly documents that zero people were present.

---

## 3. YOLO Coordinate Calculation Reference

Given pixel coordinates $(x_{\min}, y_{\min}, x_{\max}, y_{\max})$ from an image with dimensions $W \times H$:

$$
x_{\text{center}} = \frac{x_{\min} + x_{\max}}{2 \times W}
$$
$$
y_{\text{center}} = \frac{y_{\min} + y_{\max}}{2 \times H}
$$
$$
\text{width} = \frac{x_{\max} - x_{\min}}{W}
$$
$$
\text{height} = \frac{y_{\max} - y_{\min}}{H}
$$

All coordinates must satisfy:
$$
0.0 \le x_{\text{center}} \le 1.0, \quad 0.0 \le y_{\text{center}} \le 1.0, \quad 0.0 < \text{width} \le 1.0, \quad 0.0 < \text{height} \le 1.0
$$
$$
x_{\text{center}} \pm \frac{\text{width}}{2} \in [0.0, 1.0], \quad y_{\text{center}} \pm \frac{\text{height}}{2} \in [0.0, 1.0]
$$
