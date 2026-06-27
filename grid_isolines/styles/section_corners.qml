<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="4.0.3-Norrköping" styleCategories="Symbology|Labeling">
  <renderer-v2 type="categorizedSymbol" attr="pos" forceraster="0" symbollevels="0" enableorderby="0" referencescale="-1">
    <categories>
      <category value="верх" symbol="0" label="Верх (УГ)" render="true"/>
      <category value="низ" symbol="1" label="Низ (данные)" render="true"/>
      <category value="" symbol="1" label="прочее" render="true"/>
    </categories>
    <symbols>
      <symbol type="marker" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="name" type="QString" value="triangle"/>
            <Option name="color" type="QString" value="40,40,40,255"/>
            <Option name="outline_color" type="QString" value="255,255,255,255"/>
            <Option name="outline_width" type="QString" value="0.2"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="size" type="QString" value="2.8"/>
            <Option name="size_unit" type="QString" value="MM"/>
            <Option name="angle" type="QString" value="0"/>
            <Option name="offset" type="QString" value="0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="horizontal_anchor_point" type="QString" value="1"/>
            <Option name="vertical_anchor_point" type="QString" value="1"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="1" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
          <Option type="Map">
            <Option name="name" type="QString" value="line"/>
            <Option name="color" type="QString" value="40,40,40,255"/>
            <Option name="outline_color" type="QString" value="40,40,40,255"/>
            <Option name="outline_width" type="QString" value="0.5"/>
            <Option name="outline_width_unit" type="QString" value="MM"/>
            <Option name="size" type="QString" value="2"/>
            <Option name="size_unit" type="QString" value="MM"/>
            <Option name="angle" type="QString" value="90"/>
            <Option name="offset" type="QString" value="0,0"/>
            <Option name="offset_unit" type="QString" value="MM"/>
            <Option name="horizontal_anchor_point" type="QString" value="1"/>
            <Option name="vertical_anchor_point" type="QString" value="1"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fontFamily="Sans Serif" fontSize="8" fieldName="label" isExpression="0" textColor="20,20,20,255" namedStyle="Regular" multilineHeight="1.1">
        <text-buffer bufferDraw="1" bufferSize="0.8" bufferSizeUnits="MM" bufferColor="255,255,255,230"/>
      </text-style>
      <text-format wrapChar="" autoWrapLength="0"/>
      <placement placement="0" dist="1" distUnits="MM" quadOffset="0" offsetType="0"/>
      <rendering obstacle="0" upsidedownLabels="0" labelPerPart="0"/>
    </settings>
  </labeling>
  <blendMode>0</blendMode>
</qgis>
