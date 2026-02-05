<script lang="ts">
  import { DropdownMenu as DropdownMenuPrimitive } from 'bits-ui';

  type Props = DropdownMenuPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<DropdownMenuPrimitive.Trigger {...restProps}>
  {@render children?.()}
</DropdownMenuPrimitive.Trigger>
