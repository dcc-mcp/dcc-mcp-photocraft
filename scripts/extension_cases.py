"""Observable live editing scenarios, shared by facade and official Core/CLI runs.

Pillow constructs independent test expectations only. All exported evidence and
native documents are created by the real PhotoCraft process through typed tools.
"""

from headless_e2e import (
    composite,
    digest,
    layer_signature,
    load_rgba,
    pixels_equal,
    public_document,
)
from PIL import Image, ImageStat


def render(run, workspace, name, size):
    run.call("export_image", path=name)
    return load_rgba(workspace.output_root / name, size)


def layer_by_id(document, identifier):
    return next(layer for layer in document["layers"] if layer["id"] == identifier)


def close_document(run, name):
    state = run.call("document_session", action="close", index=0)["session"]
    run.check(name, state["documents"] == [])


def assert_rejection_unchanged(run, workspace, name, tool, arguments, size):
    before = run.call("document_inspect")["document"]
    pixels = render(run, workspace, name + "-before.png", size)
    result = run.reject(tool, **arguments)
    after = run.call("document_inspect")["document"]
    output = render(run, workspace, name + "-after.png", size)
    run.check(name, before == after and pixels_equal(pixels, output), error=result["error"])


def native_roundtrip(run, workspace, stem, document, expected):
    run.call("document_save_as", path=stem + ".pcraft")
    native = workspace.output_root / (stem + ".pcraft")
    native_hash = digest(native)
    close_document(run, stem + "_closed_before_reopen")
    reopened = run.call("document_open", path=stem + ".pcraft", source="output")["document"]
    run.check(
        stem + "_native_layers_preserved", public_document(reopened) == public_document(document)
    )
    pixels = render(run, workspace, stem + "-reopened.png", expected.size)
    run.check(stem + "_native_pixels_preserved", pixels_equal(pixels, expected))
    run.check(stem + "_native_file_unchanged", digest(native) == native_hash)
    return reopened


def exercise_geometry(run, workspace):
    call, check = run.call, run.check
    fixture = load_rgba(workspace.input_root / "plan.png", (96, 64))
    document = call("document_open", path="plan.png")["document"]
    layer = document["layers"][0]["id"]
    document = call("image_crop", x=8, y=8, width=80, height=48)["document"]
    crop = fixture.crop((8, 8, 88, 56))
    pixels = render(run, workspace, "geometry-crop.png", crop.size)
    check("crop_is_exact_pixel_slice", pixels_equal(pixels, crop))
    check(
        "crop_preserves_editable_pixel_layer",
        len(document["layers"]) == 1 and layer_by_id(document, layer)["kind"] == "Pixel",
    )

    document = call("canvas_resize", width=96, height=64, anchor="bottomRight")["document"]
    expanded = Image.new("RGBA", (96, 64))
    expanded.alpha_composite(crop, (16, 16))
    pixels = render(run, workspace, "geometry-canvas.png", expanded.size)
    check("canvas_expansion_preserves_pixels_and_alpha", pixels_equal(pixels, expanded))
    check("canvas_anchor_bounds", layer_by_id(document, layer)["bounds"] == [16, 16, 80, 48])
    assert_rejection_unchanged(
        run,
        workspace,
        "canvas_shrink_rejected_without_edit",
        "canvas_resize",
        {"width": 80, "height": 48},
        (96, 64),
    )

    document = call("layer_translate", layer=layer, dx=-8, dy=-8)["document"]
    translated = Image.new("RGBA", (96, 64))
    translated.alpha_composite(crop, (8, 8))
    pixels = render(run, workspace, "geometry-translate.png", translated.size)
    check("translation_exact_pixels", pixels_equal(pixels, translated))
    check("translation_bounds", layer_by_id(document, layer)["bounds"] == [8, 8, 80, 48])

    # The public transform contract uses the document origin as its pivot.
    document = call(
        "layer_transform",
        layer=layer,
        scale_x=0.5,
        translate_x=4,
        translate_y=4,
        interpolation="nearest",
    )["document"]
    scaled = Image.new("RGBA", (96, 64))
    scaled.alpha_composite(crop.resize((40, 24), Image.Resampling.NEAREST), (8, 8))
    pixels = render(run, workspace, "geometry-scale.png", scaled.size)
    check("transform_scale_exact_pixels", pixels_equal(pixels, scaled))
    check("transform_scale_bounds", layer_by_id(document, layer)["bounds"] == [8, 8, 40, 24])
    document = call(
        "layer_transform", layer=layer, rotation_degrees=90, translate_x=64, interpolation="nearest"
    )["document"]
    rotated = Image.new("RGBA", (96, 64))
    rotated.alpha_composite(
        crop.resize((40, 24), Image.Resampling.NEAREST).transpose(Image.Transpose.ROTATE_270),
        (32, 8),
    )
    pixels = render(run, workspace, "geometry-rotation.png", rotated.size)
    check("transform_rotation_exact_pixels", pixels_equal(pixels, rotated))
    native_roundtrip(run, workspace, "geometry", document, pixels)
    close_document(run, "geometry_session_clean")
    document = call("document_open", path="plan.png")["document"]
    layer = document["layers"][0]["id"]
    call("image_crop", x=0, y=0, width=32, height=32, delete_cropped_pixels=False)
    document = call("document_resize", width=64, height=64)["document"]
    check(
        "resize_tracks_actual_offcanvas_surface_bounds",
        document["width"] == document["height"] == 64
        and layer_by_id(document, layer)["bounds"] == [0, 0, 192, 128],
    )
    output = render(run, workspace, "geometry-offcanvas.png", (64, 64))
    check(
        "offcanvas_resize_visible_interior",
        # imageSize uses its own bicubic border sampling; the separate
        # nearest-transform oracle above verifies complete exact pixels.
        pixels_equal(output.crop((4, 4, 60, 60)), Image.new("RGBA", (56, 56), (255, 0, 0, 255))),
    )
    close_document(run, "offcanvas_fixture_clean")


def exercise_import_and_mask(run, workspace):
    call, check = run.call, run.check
    base = load_rgba(workspace.input_root / "gradient.png", (384, 256))
    plan = load_rgba(workspace.input_root / "plan.png", (96, 64))
    document = call("document_open", path="gradient.png")["document"]
    base_id = document["layers"][0]["id"]
    document = call("layer_import", path="plan.png", name="Original synthetic plan")["document"]
    imported = next(layer for layer in document["layers"] if layer["id"] != base_id)
    layer = imported["id"]
    check(
        "import_retains_two_editable_layers",
        len(document["layers"]) == 2
        and imported["kind"] == "Pixel"
        and imported["bounds"] == [0, 0, 96, 64]
        and imported["name"] == "Original synthetic plan",
    )
    pixels = render(run, workspace, "import-initial.png", base.size)
    check("import_composite_exact_pixels", pixels_equal(pixels, composite(base, plan, (0, 0))))
    state = call("document_session", action="list")["session"]
    check(
        "import_temporary_document_cleaned", len(state["documents"]) == 1 and state["active"] == 0
    )

    document = call("layer_translate", layer=layer, dx=32, dy=24)["document"]
    original = composite(base, plan, (32, 24))
    pixels = render(run, workspace, "import-positioned.png", base.size)
    check("imported_layer_position_exact_pixels", pixels_equal(pixels, original))
    document = call("layer_arrange", layer=layer, target=base_id, position="below")["document"]
    pixels = render(run, workspace, "import-below.png", base.size)
    check(
        "reorder_below_opaque_base_changes_pixels",
        [row["id"] for row in document["layers"]] == [base_id, layer]
        and pixels_equal(pixels, base),
    )
    document = call("layer_arrange", layer=layer, target=base_id, position="above")["document"]
    pixels = render(run, workspace, "import-above.png", base.size)
    check(
        "reorder_above_restores_pixels",
        [row["id"] for row in document["layers"]] == [layer, base_id]
        and pixels_equal(pixels, original),
    )
    document = call("layer_duplicate", layer=layer)["document"]
    duplicate = next(row for row in document["layers"] if row["id"] not in {layer, base_id})
    check(
        "duplicate_is_independent_pixel_layer",
        len(document["layers"]) == 3
        and duplicate["kind"] == "Pixel"
        and duplicate["bounds"] == [32, 24, 96, 64],
    )
    call("layer_set", layer=duplicate["id"], name="Hidden duplicate", visible=False)
    document = call("layer_select", layer=layer)["document"]
    check(
        "selected_layer_readback",
        document["activeLayer"] == layer and document["selectedLayers"] == [layer],
    )

    assert_rejection_unchanged(
        run,
        workspace,
        "missing_arrange_target_rejected_without_edit",
        "layer_arrange",
        {"layer": layer, "target": 4294967295, "position": "above"},
        base.size,
    )
    assert_rejection_unchanged(
        run,
        workspace,
        "import_traversal_rejected_without_edit",
        "layer_import",
        {"path": "../outside.png"},
        base.size,
    )

    document = call("selection_rect", x=32, y=24, width=48, height=64)["document"]
    check(
        "selection_rectangle_readback",
        document["hasSelection"] is True and document["selectionBounds"] == [32, 24, 48, 64],
    )
    call("mask_set", mode="reveal_selection", layer=layer)
    document = call("selection_clear")["document"]
    check("selection_cleared", document["hasSelection"] is False)
    local = plan.copy()
    local.paste((0, 0, 0, 0), (48, 0, 96, 64))
    masked_expected = composite(base, local, (32, 24))
    masked = render(run, workspace, "mask-selection.png", base.size)
    check(
        "local_selection_mask_exact_pixels",
        layer_by_id(document, layer)["hasMask"] is True and pixels_equal(masked, masked_expected),
    )

    call(
        "mask_paint",
        layer=layer,
        points=[{"x": 104, "y": 40}],
        size=8,
        hardness=1,
        opacity=1,
        value="reveal",
    )
    painted = render(run, workspace, "mask-painted.png", base.size)
    # Brush coverage uses engine pixel centers. Require its visible center and
    # strictly local effect without assuming its antialias implementation.
    outside_before, outside_after = masked.copy(), painted.copy()
    outside_before.paste((0, 0, 0, 0), (98, 34, 111, 47))
    outside_after.paste((0, 0, 0, 0), (98, 34, 111, 47))
    check(
        "mask_paint_reveals_only_local_pixels",
        painted.getpixel((104, 40)) == original.getpixel((104, 40))
        and painted.getpixel((104, 40)) != masked.getpixel((104, 40))
        and pixels_equal(outside_before, outside_after),
    )
    call("mask_enabled", layer=layer, enabled=False)
    pixels = render(run, workspace, "mask-disabled.png", base.size)
    check("mask_disable_restores_original_pixels", pixels_equal(pixels, original))
    document = call("mask_enabled", layer=layer, enabled=True)["document"]
    pixels = render(run, workspace, "mask-reenabled.png", base.size)
    check("mask_reenable_restores_local_pixels", pixels_equal(pixels, painted))
    assert_rejection_unchanged(
        run,
        workspace,
        "masked_resize_rejected_without_edit",
        "document_resize",
        {"width": 192, "height": 128},
        base.size,
    )
    native_roundtrip(run, workspace, "layer-proposal", document, painted)
    assert_rejection_unchanged(
        run,
        workspace,
        "inactive_mask_target_rejected_without_edit",
        "mask_enabled",
        {"layer": layer, "enabled": False},
        base.size,
    )
    # Native files restore the layer tree; the active selection is session
    # state. Select the retained masked layer before testing a further edit.
    call("layer_select", layer=layer)
    call("mask_enabled", layer=layer, enabled=False)
    pixels = render(run, workspace, "native-mask-disabled.png", base.size)
    check("native_mask_remains_editable", pixels_equal(pixels, original))
    call("mask_enabled", layer=layer, enabled=True)
    pixels = render(run, workspace, "native-mask-reenabled.png", base.size)
    check("native_mask_reenabled_exact_pixels", pixels_equal(pixels, painted))
    close_document(run, "import_mask_session_clean")


def exercise_extensions(run, workspace):
    catalog = run.call("capabilities_query", query="layer.translate", limit=10)
    run.check(
        "capability_catalog_query",
        isinstance(catalog["upstream"], dict)
        and any(
            entry["id"] == "layer.translate"
            and entry["exposure_status"] == "partial"
            and entry["adapter_tools"] == ["layer_translate"]
            for entry in catalog["entries"]
        ),
    )
    first = run.call("capabilities_query", limit=1)
    second = run.call("capabilities_query", offset=1, limit=1)
    run.check(
        "capability_catalog_pagination",
        first["next_offset"] == 1
        and len(first["entries"]) == len(second["entries"]) == 1
        and first["entries"][0]["id"] != second["entries"][0]["id"]
        and first["total"] == second["total"],
    )
    exercise_geometry(run, workspace)
    exercise_import_and_mask(run, workspace)
    exercise_tones(run, workspace)
    exercise_fills_and_groups(run, workspace)
    exercise_selection(run, workspace)
    exercise_noise(run, workspace)
    exercise_noise_from_painted_mask(run, workspace)


def exercise_tones(run, workspace):
    call, check = run.call, run.check
    baseline = load_rgba(workspace.input_root / "tones.png", (50, 16))
    document = call("document_open", path="tones.png")["document"]
    cases = (
        ("brightness_contrast", {"brightness": 30, "contrast": 10}),
        ("exposure", {"exposure": 1}),
        ("levels", {"in_black": 80, "in_white": 176}),
        ("curves", {"points": [[0, 255], [255, 0]]}),
    )
    effects = []
    for kind, parameters in cases:
        document = call("adjustment_tone", kind=kind, **parameters)["document"]
        tone = document["layers"][0]
        check(
            "tone_" + kind + "_editable_layer",
            tone["kind"] == "Adjustment" and bool(tone.get("adjustment")),
        )
        output = render(run, workspace, "tone-" + kind + ".png", baseline.size)
        sample = [output.getpixel((x, 8))[0] for x in (5, 15, 25, 35, 45)]
        if kind in {"brightness_contrast", "exposure"}:
            observable = sample[2] > 128 and sample[4] == 255
        elif kind == "levels":
            observable = sample[1] == 0 and sample[3] == 255
        else:
            observable = sample[0] == 255 and sample[4] == 0 and sample[1] > sample[3]
        check(
            "tone_" + kind + "_pixel_effect",
            observable and not pixels_equal(output, baseline),
            gray_samples=sample,
        )
        effects.append(output.tobytes())
        document = call("layer_set", layer=tone["id"], visible=False)["document"]
        hidden = render(run, workspace, "tone-" + kind + "-hidden.png", baseline.size)
        check("tone_" + kind + "_nondestructive", pixels_equal(hidden, baseline))
    check("all_tone_kinds_have_visible_effect", len(set(effects)) == len(cases))
    # Persist all four adjustment definitions, even though hidden for a clean
    # exact reference. Each layer's enabled rendering was tested above.
    native_roundtrip(run, workspace, "tones", document, baseline)
    close_document(run, "tone_session_clean")


def exercise_fills_and_groups(run, workspace):
    call, check = run.call, run.check
    size = (64, 48)
    base = Image.new("RGBA", size, (64, 96, 128, 255))
    document = call("document_new", width=64, height=48, name="Fill fixture", background="#406080")[
        "document"
    ]
    document = call("fill_layer", color="#ff0000", name="Editable red fill")["document"]
    fill_id = document["layers"][0]["id"]
    red = Image.new("RGBA", size, (255, 0, 0, 255))
    output = render(run, workspace, "fill-solid.png", size)
    check(
        "solid_fill_exact_pixels",
        pixels_equal(output, red) and document["layers"][0]["kind"] == "Fill",
    )
    sample = call("document_sample_pixel", x=10, y=10)
    check(
        "native_sample_agrees_with_export",
        sample["source"] == "native_composite"
        and sample["rgba"] == [1, 0, 0, 1]
        and output.getpixel((10, 10)) == (255, 0, 0, 255),
    )
    document = call("layer_set", layer=fill_id, fill=0)["document"]
    output = render(run, workspace, "fill-zero.png", size)
    check(
        "fill_opacity_zero_preserves_underlying_pixels",
        layer_by_id(document, fill_id)["fill"] == 0 and pixels_equal(output, base),
    )
    document = call("layer_set", layer=fill_id, fill=1, blend="multiply", clipped=True)["document"]
    output = render(run, workspace, "fill-multiply.png", size)
    row = layer_by_id(document, fill_id)
    check(
        "blend_and_clipping_readback_and_pixels",
        row["blend"].casefold() == "multiply"
        and row["clipped"] is True
        and pixels_equal(output, Image.new("RGBA", size, (64, 0, 0, 255))),
    )
    call("layer_set", layer=fill_id, blend="normal", clipped=False)
    before_group = call("document_inspect")["document"]
    document = call("layer_group", name="Editable empty group")["document"]
    group = document["layers"][0]
    output = render(run, workspace, "fill-group.png", size)
    check(
        "group_is_editable_and_nonflattening",
        group["kind"] == "Group"
        and group["children"] == []
        and layer_signature({"layers": document["layers"][1:]}) == layer_signature(before_group)
        and pixels_equal(output, red),
    )
    document = call("layer_delete", layer=group["id"])["document"]
    output = render(run, workspace, "fill-group-deleted.png", size)
    check(
        "deleting_group_restores_prior_pixels",
        public_document(document) == public_document(before_group) and pixels_equal(output, red),
    )
    call("layer_delete", layer=fill_id)
    output = render(run, workspace, "fill-deleted.png", size)
    check("deleting_visible_fill_restores_base", pixels_equal(output, base))

    document = call(
        "fill_layer", color="#ff0000", end_color="#0000ff", name="Editable gradient", angle=0
    )["document"]
    output = render(run, workspace, "fill-gradient.png", size)
    colors = set(output.getdata())
    check(
        "gradient_fill_has_spatial_color_effect",
        document["layers"][0]["kind"] == "Fill"
        and len(colors) > 10
        and all(green == 0 and alpha == 255 for _, green, _, alpha in colors)
        and output.getpixel((4, 24)) != output.getpixel((60, 24)),
    )
    native_roundtrip(run, workspace, "fills", document, output)
    close_document(run, "fill_session_clean")


def exercise_selection(run, workspace):
    call, check = run.call, run.check
    size = (64, 48)
    document = call(
        "document_new", width=64, height=48, name="Selection fixture", background="#000000"
    )["document"]
    layer = document["layers"][0]["id"]
    call("selection_rect", x=8, y=8, width=24, height=24)
    call("selection_fill", layer=layer, color="#ff0000")
    expected = Image.new("RGBA", size, (0, 0, 0, 255))
    expected.paste((255, 0, 0, 255), (8, 8, 32, 32))
    output = render(run, workspace, "selection-fill.png", size)
    check("selection_fill_exact_pixels", pixels_equal(output, expected))
    call("selection_invert")
    call("selection_fill", layer=layer, color="#0000ff")
    expected = Image.new("RGBA", size, (0, 0, 255, 255))
    expected.paste((255, 0, 0, 255), (8, 8, 32, 32))
    output = render(run, workspace, "selection-inverted.png", size)
    check("inverted_selection_fill_exact_pixels", pixels_equal(output, expected))
    document = call("selection_all")["document"]
    check(
        "selection_all_canvas_readback",
        document["hasSelection"] is True and document["selectionBounds"] == [0, 0, 64, 48],
    )
    call("selection_fill", layer=layer, color="#00ff00")
    document = call("selection_clear")["document"]
    output = render(run, workspace, "selection-all-filled.png", size)
    check(
        "selection_all_fills_entire_canvas",
        not document["hasSelection"]
        and pixels_equal(output, Image.new("RGBA", size, (0, 255, 0, 255))),
    )
    assert_rejection_unchanged(
        run,
        workspace,
        "fill_without_selection_rejected_without_edit",
        "selection_fill",
        {"layer": layer, "color": "#ffffff"},
        size,
    )
    native_roundtrip(run, workspace, "selection", document, output)
    close_document(run, "extension_final_session_empty")


def noise_std(image, rectangle):
    return ImageStat.Stat(image.crop(rectangle).convert("RGB")).stddev[0]


def exercise_noise(run, workspace):
    call, check = run.call, run.check
    size = (128, 96)
    baseline = load_rgba(workspace.input_root / "noise.png", size)
    parameters = {
        "strength": 8,
        "preserve_details": 0,
        "reduce_color_noise": 100,
        "sharpen_details": 0,
    }
    document = call("document_open", path="noise.png")["document"]
    original_id = document["layers"][0]["id"]
    document = call("layer_duplicate", layer=original_id)["document"]
    filtered_id = document["layers"][0]["id"]
    call("selection_clear")
    document = call("noise_reduce", layer=filtered_id, **parameters)["document"]
    filtered = render(run, workspace, "noise-reduced.png", size)
    before_std = noise_std(baseline, (40, 24, 88, 72))
    after_std = noise_std(filtered, (40, 24, 88, 72))
    check(
        "noise_reduce_lowers_noise_and_retains_markers",
        after_std < before_std * 0.95
        and filtered.getpixel((16, 16)) == (255, 255, 255, 255)
        and filtered.getpixel((112, 80)) == (0, 0, 0, 255)
        and len(document["layers"]) == 2
        and document["layers"][-1]["id"] == original_id,
        before_stddev=before_std,
        after_stddev=after_std,
    )
    call("selection_rect", x=0, y=0, width=64, height=96)
    call("mask_set", layer=filtered_id, mode="reveal_selection")
    document = call("selection_clear")["document"]
    visible = render(run, workspace, "noise-local-mask.png", size)
    expected = baseline.copy()
    expected.paste(filtered.crop((0, 0, 64, 96)), (0, 0))
    check("noise_before_after_mask_is_exact", pixels_equal(visible, expected))
    native_roundtrip(run, workspace, "noise-editable", document, visible)
    call("layer_select", layer=filtered_id)
    call("mask_enabled", layer=filtered_id, enabled=False)
    unmasked = render(run, workspace, "noise-native-mask-disabled.png", size)
    check("noise_native_mask_remains_editable", pixels_equal(unmasked, filtered))
    call("mask_enabled", layer=filtered_id, enabled=True)
    remasked = render(run, workspace, "noise-native-mask-restored.png", size)
    check("noise_native_mask_restores_before_after", pixels_equal(remasked, expected))
    close_document(run, "noise_mask_session_clean")

    document = call("document_open", path="noise.png")["document"]
    layer = document["layers"][0]["id"]
    rectangle = (32, 16, 96, 80)
    call("selection_rect", x=32, y=16, width=64, height=64)
    call("noise_reduce", layer=layer, **parameters)
    local = render(run, workspace, "noise-selected.png", size)
    outside_before, outside_after = baseline.copy(), local.copy()
    outside_before.paste((0, 0, 0, 0), rectangle)
    outside_after.paste((0, 0, 0, 0), rectangle)
    check(
        "selected_noise_reduction_preserves_outside_exactly",
        pixels_equal(outside_before, outside_after)
        and noise_std(local, (40, 24, 88, 72)) < before_std * 0.95,
    )
    call("selection_clear")
    document = call("noise_despeckle", layer=layer)["document"]
    despeckled = render(run, workspace, "noise-despeckled.png", size)
    untreated = (32, 0, 96, 12)
    before_despeckle = noise_std(local, untreated)
    after_despeckle = noise_std(despeckled, untreated)
    check(
        "despeckle_reduces_noise_in_untreated_region",
        after_despeckle < before_despeckle
        and not pixels_equal(despeckled, local)
        and despeckled.getpixel((16, 16)) == (255, 255, 255, 255)
        and despeckled.getpixel((112, 80)) == (0, 0, 0, 255),
        before_stddev=before_despeckle,
        after_stddev=after_despeckle,
    )
    native_roundtrip(run, workspace, "noise-selected", document, despeckled)
    close_document(run, "noise_final_session_empty")


def exercise_noise_from_painted_mask(run, workspace):
    call, check = run.call, run.check
    size = (128, 96)
    baseline = load_rgba(workspace.input_root / "noise.png", size)
    parameters = {
        "strength": 8,
        "preserve_details": 0,
        "reduce_color_noise": 100,
        "sharpen_details": 0,
    }
    document = call("document_open", path="noise.png")["document"]
    layer = document["layers"][0]["id"]
    call("mask_set", layer=layer, mode="hide_all")
    call(
        "mask_paint", layer=layer, points=[{"x": 64, "y": 48}], size=32, hardness=1, value="reveal"
    )
    masked = render(run, workspace, "noise-painted-mask.png", size)
    alpha = list(masked.getchannel("A").getdata())
    check(
        "painted_noise_mask_has_local_coverage",
        masked.getchannel("A").getbbox() is not None
        and sum(a == 255 for a in alpha) > 100
        and sum(a == 0 for a in alpha) > 10000,
    )
    call("mask_enabled", layer=layer, enabled=False)
    unmasked = render(run, workspace, "noise-painted-mask-disabled.png", size)
    check("disabled_noise_mask_retains_full_input", pixels_equal(unmasked, baseline))
    document = call("selection_from_mask", layer=layer)["document"]
    bounds = document["selectionBounds"]
    check(
        "painted_mask_loads_bounded_selection",
        document["hasSelection"] is True
        and 44 <= bounds[0] <= 52
        and 28 <= bounds[1] <= 36
        and 24 <= bounds[2] <= 40
        and 24 <= bounds[3] <= 40,
    )
    call("noise_reduce", layer=layer, **parameters)
    local = render(run, workspace, "noise-from-painted-mask.png", size)
    before, after = list(baseline.getdata()), list(local.getdata())
    unchanged_outside = all(a == b for a, b, mask in zip(before, after, alpha) if mask == 0)
    check(
        "painted_mask_selection_limits_noise",
        unchanged_outside
        and noise_std(local, (58, 42, 70, 54)) < noise_std(baseline, (58, 42, 70, 54)) * 0.95,
    )
    call("selection_clear")
    call("selection_from_mask", layer=layer, invert=True)
    call("noise_reduce", layer=layer, **parameters)
    inverted = render(run, workspace, "noise-from-inverted-mask.png", size)
    inverted_pixels = list(inverted.getdata())
    check(
        "inverted_mask_selection_preserves_original_interior",
        all(a == b for a, b, mask in zip(after, inverted_pixels, alpha) if mask == 255)
        and noise_std(inverted, (32, 0, 96, 12)) < noise_std(local, (32, 0, 96, 12)) * 0.95,
    )
    document = call("selection_clear")["document"]
    native_roundtrip(run, workspace, "noise-painted-selection", document, inverted)
    call("layer_select", layer=layer)
    call("mask_enabled", layer=layer, enabled=True)
    visible = render(run, workspace, "noise-painted-native-mask.png", size)
    check(
        "painted_noise_mask_native_coverage_preserved",
        visible.getchannel("A").tobytes() == masked.getchannel("A").tobytes(),
    )
    close_document(run, "painted_mask_noise_session_clean")
