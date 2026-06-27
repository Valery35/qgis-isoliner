<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="4.0.3-Norrköping" styleCategories="Symbology|Labeling">
  <renderer-v2 type="singleSymbol" forceraster="0" symbollevels="0" enableorderby="0" referencescale="-1">
    <symbols>
      <symbol type="line" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="line_color" type="QString" value="150,150,150,200"/>
            <Option name="line_style" type="QString" value="solid"/>
            <Option name="line_width" type="QString" value="0.2"/>
            <Option name="line_width_unit" type="QString" value="MM"/>
            <Option name="use_custom_dash" type="QString" value="1"/>
            <Option name="customdash" type="QString" value="3;2"/>
            <Option name="customdash_unit" type="QString" value="MM"/>
            <Option name="capstyle" type="QString" value="flat"/>
            <Option name="joinstyle" type="QString" value="bevel"/>
            <Option name="offset" type="QString" value="0"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fontFamily="Sans Serif" fontSize="7" fieldName="label" isExpression="0" textColor="80,80,80,255" namedStyle="Regular">
        <text-buffer bufferDraw="1" bufferSize="0.8" bufferSizeUnits="MM" bufferColor="255,255,255,230"/>
      </text-style>
      <placement placement="2" placementFlags="10" dist="1" distUnits="MM" repeatDistance="0" lineAnchorPercent="0" lineAnchorClipping="0" lineAnchorType="0" lineAnchorTextPoint="2"/>
      <rendering obstacle="0" upsidedownLabels="0" mergeLines="1" labelPerPart="0"/>
    </settings>
  </labeling>
  <blendMode>0</blendMode>
</qgis>
